Variant Monitoring Service
================================================================================

- [Variant Monitoring Service](#variant-monitoring-service)
  - [Service Description](#service-description)
    - [Name \& responsibility](#name--responsibility)
    - [Description](#description)
    - [Consumed Events](#consumed-events)
    - [Published Events](#published-events)
    - [Data states \& persistence model](#data-states--persistence-model)
    - [Major Business Rules](#major-business-rules)
    - [Permissions \& Access Control](#permissions--access-control)
    - [Change Management](#change-management)
      - [Versioning strategy](#versioning-strategy)
      - [Release management](#release-management)
  - [Infrastructure \& Deployment](#infrastructure--deployment)
    - [Stateless](#stateless)
    - [CDK Commands](#cdk-commands)
    - [Stacks](#stacks)
  - [Development](#development)
    - [Project Structure](#project-structure)
    - [Setup](#setup)
      - [Requirements](#requirements)
      - [Install Dependencies](#install-dependencies)
    - [Linting \& Formatting](#linting--formatting)
    - [Testing](#testing)
  - [Glossary \& References](#glossary--references)


Service Description
--------------------------------------------------------------------------------

### Name & responsibility

**Variant Monitoring** — extracts allele frequencies (AF) from DRAGEN WGS BatchControl VCF outputs and publishes the results for QC tracking and NATA accreditation reporting.

### Description

This service monitors germline variant allele frequencies at 10 predefined loci in WGS BatchControl positive-control samples (GIAB cell lines HG001/NA12878, HG002/NA24385, HG005/NA24631) to detect drift in DRAGEN pipeline performance over time.

Flow:

1. An EventBridge rule filters `WorkflowRunStateChange` (SUCCEEDED) events from `orcabus.workflowmanager` for `dragen-wgts-dna` workflows.
2. The matching event triggers the `ExtractVariantAfFunction` Lambda.
3. The Lambda resolves the DRAGEN hard-filtered VCF location from `payload.data.engineParameters.outputUri`, downloads the VCF and tabix index to ephemeral `/tmp` storage, and queries the 10 monitoring sites using pysam.
4. A `VariantMonitoringResult` event is emitted to the `OrcaBusMain` bus with the AF readings for downstream consumption (storage and QC plotting).

**Monitoring sites (10 GIAB PASS germline variants):**

| # | Locus | Ref > Alt |
|---|-------|-----------|
| 1 | chr2:47803699 | A > T |
| 2 | chr5:112827157 | T > C |
| 3 | chr11:44130027 | T > A |
| 4 | chr15:40161296 | G > A |
| 5 | chr15:40185630 | G > A |
| 6 | chr15:40199751 | A > G |
| 7 | chr16:68823538 | T > C |
| 8 | chr19:10996457 | T > C |
| 9 | chr19:11058220 | A > G |
| 10 | chr22:20985799 | T > C |

The site definitions are bundled with the Lambda in `app/variant_monitoring/references/varmon_10_sites.vcf`.

### Consumed Events

| DetailType | Source | Description |
|---|---|---|
| `WorkflowRunStateChange` | `orcabus.workflowmanager` | Fired on every state transition of a workflow run. Filtered to `workflow.name = dragen-wgts-dna` and `status = SUCCEEDED`. |

### Published Events

| DetailType | Source | Description |
|---|---|---|
| `VariantMonitoringResult` | `orcabus.variantmonitoring` | Emitted after successfully extracting AFs from a DRAGEN hard-filtered VCF. Contains `portalRunId`, `libraryId`, `subjectId`, `individualId`, `analysisName`, `outputUri`, `extractedAt`, and `monitoringSites` (10 AF readings). |

### Data states & persistence model

This service is stateless. No data is persisted internally. The `VariantMonitoringResult` event is intended to be consumed by a downstream service responsible for storing AF readings and triggering QC plots.

### Major Business Rules

- Only `SUCCEEDED` `dragen-wgts-dna` workflow runs are processed (enforced at both the EventBridge rule and Lambda levels).
- If `payload.data.engineParameters.outputUri` is absent, the Lambda raises a `ValueError` and fails (triggering standard retry/DLQ behaviour).
- If a hard-filtered VCF or its tabix index cannot be found under `outputUri`, the Lambda raises `FileNotFoundError`.
- When `payload.data.outputs.dragenGermlineVariantCallingOutputRelPath` is present, the VCF search is narrowed to that subdirectory; otherwise the full `outputUri` prefix is scanned.
- A site with no matching variant in the VCF is reported with `af=0.0`, `dp=0`, `variant_emitted=false` — it is not treated as an error.
- A failed `put_events` call (non-zero `FailedEntryCount`) raises a `RuntimeError`, causing the Lambda to fail and triggering the standard retry/DLQ behaviour.

### Permissions & Access Control

The Lambda execution role is granted:

- `events:PutEvents` on the `OrcaBusMain` EventBridge bus.
- `s3:GetObject` and `s3:ListBucket` on `pipeline-*-cache-*` buckets (covers beta, gamma, and prod ICAv2 DRAGEN cache buckets).
- `secretsmanager:GetSecretValue` on `orcabus/token-service-jwt*`.
- `ssm:GetParameter` on `/hosted_zone/umccr/name`.

### Change Management

#### Versioning strategy

Manual tagging of git commits following Semantic Versioning (semver) guidelines.

#### Release management

The service employs a fully automated CI/CD pipeline that automatically builds and releases all changes to the `main` branch across `beta`, `gamma`, and `prod` environments.


Infrastructure & Deployment
--------------------------------------------------------------------------------

Infrastructure is managed via CDK. The `deployMode` context variable selects the entry point.

### Stateless

- **`ExtractVariantAfFunction`** — Python 3.12 ARM64 Lambda (1 GB memory, 2 GB ephemeral storage, 5 min timeout). Bundled via `PythonLayerVersion` from `app/requirements.txt`. Entrypoint: `variant_monitoring/lambdas/extract_variant_af.py::lambda_handler`.
- **`WorkflowRunStateChangeRule`** — EventBridge rule on `OrcaBusMain` matching `WorkflowRunStateChange` events where `source = orcabus.workflowmanager`, `detail.workflow.name = dragen-wgts-dna`, and `detail.status = SUCCEEDED`.

**S3 cache buckets by stage:**

| Stage | Bucket |
|---|---|
| BETA | `pipeline-dev-cache-503977275616-ap-southeast-2` |
| GAMMA | `pipeline-stg-cache-503977275616-ap-southeast-2` |
| PROD | `pipeline-prod-cache-503977275616-ap-southeast-2` |

### CDK Commands

- **`cdk-stateless`**: Deploys stacks containing stateless resources (Lambda, EventBridge rules).

All deployments go through the `DeploymentStackPipeline` construct, which handles cross-account role assumptions and applies the correct per-environment configuration from `config.ts`.

```sh
# Deploy the toolchain pipeline stack (sets up CodePipeline in the bastion account)
pnpm cdk-stateless deploy -e OrcaBusStatelessVariantMonitoringStack

# Manually deploy directly to the beta (dev) environment (bypasses the pipeline)
AWS_PROFILE=umccr-dev-pu pnpm cdk-stateless deploy \
  OrcaBusStatelessVariantMonitoringStack/DeploymentPipeline/OrcaBusBeta/VariantMonitoringStack \
  --require-approval never -e
```

### Stacks

```sh
pnpm cdk-stateless ls
```

Expected output:

```
OrcaBusStatelessVariantMonitoringStack
OrcaBusStatelessVariantMonitoringStack/DeploymentPipeline/OrcaBusBeta/VariantMonitoringStack  (OrcaBusBeta-VariantMonitoringStack)
OrcaBusStatelessVariantMonitoringStack/DeploymentPipeline/OrcaBusGamma/VariantMonitoringStack (OrcaBusGamma-VariantMonitoringStack)
OrcaBusStatelessVariantMonitoringStack/DeploymentPipeline/OrcaBusProd/VariantMonitoringStack  (OrcaBusProd-VariantMonitoringStack)
```


Development
--------------------------------------------------------------------------------

### Project Structure

```
.
├── app/                              # Python application
│   ├── variant_monitoring/
│   │   ├── lambdas/
│   │   │   └── extract_variant_af.py # Lambda handler
│   │   ├── models.py                 # Pydantic event models
│   │   └── references/
│   │       └── varmon_10_sites.vcf   # Bundled monitoring site definitions
│   ├── tests/                        # Python unit tests
│   └── requirements.txt
├── bin/
│   └── deploy.ts                     # CDK entry point
├── infrastructure/
│   ├── stage/
│   │   ├── constants.ts              # Event source/type/filter constants
│   │   ├── config.ts                 # Per-environment stack props
│   │   └── deployment-stack.ts       # Runtime resource definitions
│   └── toolchain/
│       ├── stateless-stack.ts        # CodePipeline setup
└── test/                             # CDK infrastructure tests (cdk-nag)
```

### Setup

#### Requirements

```sh
node --version
# v22.9.0

npm install --global corepack@latest
corepack enable pnpm
```

#### Install Dependencies

```sh
make install
```

### Linting & Formatting

Pre-commit hooks enforce checks on every commit. Manual checks:

```sh
# Lint TypeScript and Python
make check-all

# Auto-fix ESLint / Prettier issues
make fix
```

### Testing

```sh
# Python unit tests (no Docker required)
cd app && make test

# CDK infrastructure tests (requires Docker Desktop)
pnpm test
```

> **Note:** CDK tests synthesize the Lambda layer using Docker. Start Docker Desktop before running `pnpm test` locally.

31 Python unit tests at 100% coverage, 4 CDK tests. For a full breakdown of what each test covers and smoke test results, see [`docs/testing.md`](docs/testing.md).


Glossary & References
--------------------------------------------------------------------------------

For general OrcaBus platform terms see the [platform documentation](https://github.com/OrcaBus/wiki/blob/main/orcabus-platform/README.md#glossary--references).

| Term | Description |
|---|---|
| WRSC | `WorkflowRunStateChange` — OrcaBus event emitted by the Workflow Manager on every state transition |
| `portalRunId` | Unique identifier for a workflow run, used to correlate events across services |
| `OrcaBusMain` | The shared AWS EventBridge event bus used by all OrcaBus services |
| GIAB | Genome in a Bottle — consortium providing reference cell lines (HG001/HG002/HG005) used as positive controls |
| AF | Allele Frequency — fraction of reads supporting the alternate allele at a given locus |
| DP | Read depth at a given locus |
| NATA | National Association of Testing Authorities — Australian laboratory accreditation body; AF drift tracking supports accreditation reporting |
| BatchControl | WGS positive-control sample run alongside clinical samples to monitor pipeline performance |
