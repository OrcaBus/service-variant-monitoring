# Test Coverage

31 Python unit tests, 100% coverage. 4 CDK infrastructure tests.

- [Running Tests](#running-tests)
- [Python Unit Tests](#python-unit-tests)
  - [TestHandler](#testhandler)
  - [TestModels](#testmodels)
  - [TestFindVcf](#testfindvcf)
  - [TestLoadMonitoringSites](#testloadmonitoringsites)
  - [TestExtractAllSites](#testextractallsites)
- [CDK Infrastructure Tests](#cdk-infrastructure-tests)
- [Smoke Test](#smoke-test)

## Running Tests

```sh
# Python unit tests (no Docker required)
cd app && make test

# CDK infrastructure tests (requires Docker Desktop)
pnpm test
```

## Python Unit Tests

Tests live in `app/tests/`. Fixtures are defined in `app/tests/conftest.py`.

AWS clients (`s3_client`, `events_client`) are patched with static-credential sessions in every test via an `autouse` fixture — no real AWS credentials are needed. S3 and EventBridge calls are intercepted by [moto](https://github.com/getmoto/moto) where `@mock_aws` is applied. VCF operations use real `pysam` against small in-memory VCF fixtures built by conftest.

### TestHandler

End-to-end tests for `lambda_handler`. S3 and EventBridge are mocked.

| Test                                                          | What it covers                                                                                                                             |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `test_handler_skips_non_succeeded`                            | RUNNING event returns `skipped=True` without touching S3 or EventBridge                                                                    |
| `test_handler_success`                                        | SUCCEEDED event returns `statusCode=200` and an `eventId`                                                                                  |
| `test_handler_emits_correct_event_metadata`                   | `put_events` is called with `source=orcabus.variantmonitoring`, `detail-type=VariantMonitoringResult`, correct bus name                    |
| `test_handler_detail_contains_expected_fields`                | Emitted detail contains `portalRunId`, `libraryId`, `subjectId`, `individualId`, `outputUri`, `analysisName`, 10 monitoring sites          |
| `test_handler_raises_on_eventbridge_failure`                  | Non-zero `FailedEntryCount` raises `RuntimeError` → Lambda fails and triggers retry/DLQ                                                    |
| `test_handler_raises_when_vcf_not_found`                      | VCF absent from S3 raises `FileNotFoundError`                                                                                              |
| `test_handler_raises_when_output_uri_missing`                 | Missing `payload.data.engineParameters.outputUri` raises `ValueError`                                                                      |
| `test_handler_uses_germline_output_path_when_present`         | `find_hard_filtered_vcf` is called with the narrowed germline subdirectory URI when `dragenGermlineVariantCallingOutputRelPath` is present |
| `test_handler_falls_back_to_output_uri_without_germline_path` | `find_hard_filtered_vcf` is called with the full `outputUri` when `outputs` is empty                                                       |

### TestModels

Pydantic parsing tests for `IncomingEvent` and `VariantMonitoringResultDetail`.

| Test                                                   | What it covers                                                                                      |
| ------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| `test_incoming_event_parses_detail_type`               | `detail-type` key (hyphenated) maps to `detail_type` field via alias                                |
| `test_incoming_event_parses_portal_run_id`             | `portalRunId` parsed from `detail`                                                                  |
| `test_incoming_event_parses_library_from_payload_tags` | `libraryId` parsed from nested `payload.data.tags`                                                  |
| `test_incoming_event_parses_status`                    | `status` field parsed correctly                                                                     |
| `test_incoming_event_parses_output_uri`                | `outputUri` parsed from nested `payload.data.engineParameters`                                      |
| `test_result_detail_serializes_monitoring_sites`       | `VariantMonitoringResultDetail.model_dump_json()` round-trips correctly including `monitoringSites` |

### TestFindVcf

Unit tests for `find_hard_filtered_vcf` (S3 VCF discovery).

| Test                                      | What it covers                                                                                                |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `test_find_hard_filtered_vcf_success`     | Returns correct `(bucket, vcf_key, tbi_key)` when both files exist                                            |
| `test_find_hard_filtered_vcf_not_found`   | Raises `FileNotFoundError` when no `.hard-filtered.vcf.gz` exists under the prefix                            |
| `test_find_hard_filtered_vcf_tbi_missing` | VCF found but `.tbi` absent → `head_object` raises → `FileNotFoundError` with "Tabix index not found" message |

### TestLoadMonitoringSites

Unit tests for `load_monitoring_sites` (reads monitoring site definitions from a VCF).

| Test                        | What it covers                                                                  |
| --------------------------- | ------------------------------------------------------------------------------- |
| `test_loads_sites_from_vcf` | Returns correct `(chrom, pos, ref, alt)` tuples from a single-site VCF          |
| `test_loads_sites_no_alts`  | Monomorphic site (ALT=`.`) produces `alt='.'` rather than crashing              |
| `test_raises_on_empty_vcf`  | VCF with header but no records raises `ValueError("No monitoring sites found")` |

### TestExtractAllSites

Unit tests for `extract_af_at_site` and `extract_all_sites` using real pysam against small bgzipped+indexed VCF fixtures.

| Test                                                          | What it covers                                                                                                                   |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `test_extract_af_at_site_found`                               | PASS variant returns correct `dp`, `af`, `filter_status`, `variant_emitted=True`                                                 |
| `test_extract_af_at_site_not_emitted`                         | Position absent from VCF returns `dp=0`, `af=0.0`, `variant_emitted=False`                                                       |
| `test_extract_af_at_site_filtered`                            | Filtered variant (e.g. `LowQual`) still returns `variant_emitted=True` with correct `filter_status`                              |
| `test_extract_af_at_site_allele_mismatch`                     | Correct position but wrong alt allele returns `variant_emitted=False`                                                            |
| `test_extract_af_at_site_wrong_pos_skipped`                   | Deletion spanning the query window (different `pos`) is returned by tabix but skipped by the pos check → `variant_emitted=False` |
| `test_extract_af_at_site_wrong_ref_skipped`                   | Record at correct pos but wrong ref is skipped → `variant_emitted=False`                                                         |
| `test_extract_af_at_site_pysam_exception_returns_not_emitted` | pysam exception during `fetch` is caught, logged as warning, site returns `variant_emitted=False` — Lambda does not crash        |
| `test_extract_all_sites_uses_bundled_vcf_when_no_regions_fp`  | `extract_all_sites(vcf_path)` with no `regions_fp` loads the bundled `varmon_10_sites.vcf` and returns 10 results                |
| `test_extract_all_sites`                                      | Returns one result per site in the regions VCF with correct values                                                               |
| `test_extract_all_sites_mixed`                                | Mix of PASS, filtered, and allele-mismatch sites all handled correctly in a single pass                                          |

## CDK Infrastructure Tests

4 tests in `test/toolchain.test.ts`. Synthesises the full `VariantMonitoringStack` and asserts `cdk-nag` compliance (no high-severity security findings).

Requires Docker Desktop to build the Lambda layer during synthesis.

## Smoke Test

`bin/smoke_test.py` invokes the deployed Lambda directly against a real S3 VCF. Two modes:

```sh
# Use an existing VCF already in S3 (no upload needed)
python3 bin/smoke_test.py --vcf-s3-uri s3://<bucket>/<path>/L2301217.hard-filtered.vcf.gz

# Crop a local VCF to 10 sites, upload, then invoke
python3 bin/smoke_test.py /path/to/L2301217.hard-filtered.vcf.gz --stage beta
```

Validated against 6 germline runs (L2301217 × 4, L2600126 × 1, L2600140 × 1) in `beta`. Results showed correct heterozygous (~0.5) and homozygous (~1.0) AFs at called sites, and `variant_emitted=False` at sites absent from a given sample's genotype.

### Baseline Results (beta, 2026-04-01)

Sites not called (`variant_emitted=False`) are expected — they reflect the genotype of the specific GIAB cell line, not a pipeline failure. Heterozygous sites show AF ~0.5, homozygous sites show AF ~1.0.

**L2301217** — `NA12878 / HG001` — 5/10 sites called (portalRunId: `20260315ff1641fe`)

| Locus          | Ref > Alt | DP  | AF    | Called |
| -------------- | --------- | --- | ----- | ------ |
| chr2:47803699  | A > T     | 0   | —     | ✗      |
| chr5:112827157 | T > C     | 0   | —     | ✗      |
| chr11:44130027 | T > A     | 48  | 0.542 | ✓ HET  |
| chr15:40161296 | G > A     | 56  | 0.536 | ✓ HET  |
| chr15:40185630 | G > A     | 59  | 0.509 | ✓ HET  |
| chr15:40199751 | A > G     | 48  | 0.438 | ✓ HET  |
| chr16:68823538 | T > C     | 0   | —     | ✗      |
| chr19:10996457 | T > C     | 0   | —     | ✗      |
| chr19:11058220 | A > G     | 0   | —     | ✗      |
| chr22:20985799 | T > C     | 49  | 0.510 | ✓ HET  |

**L2600126** — 5/10 sites called (portalRunId: `202602286c62f514`)

| Locus          | Ref > Alt | DP  | AF    | Called |
| -------------- | --------- | --- | ----- | ------ |
| chr2:47803699  | A > T     | 0   | —     | ✗      |
| chr5:112827157 | T > C     | 53  | 0.547 | ✓ HET  |
| chr11:44130027 | T > A     | 0   | —     | ✗      |
| chr15:40161296 | G > A     | 0   | —     | ✗      |
| chr15:40185630 | G > A     | 38  | 0.447 | ✓ HET  |
| chr15:40199751 | A > G     | 34  | 0.500 | ✓ HET  |
| chr16:68823538 | T > C     | 52  | 0.519 | ✓ HET  |
| chr19:10996457 | T > C     | 0   | —     | ✗      |
| chr19:11058220 | A > G     | 0   | —     | ✗      |
| chr22:20985799 | T > C     | 44  | 1.000 | ✓ HOM  |

**L2600140** — 9/10 sites called (portalRunId: `20260311e01885f9`)

| Locus          | Ref > Alt | DP  | AF    | Called |
| -------------- | --------- | --- | ----- | ------ |
| chr2:47803699  | A > T     | 42  | 0.429 | ✓ HET  |
| chr5:112827157 | T > C     | 52  | 1.000 | ✓ HOM  |
| chr11:44130027 | T > A     | 0   | —     | ✗      |
| chr15:40161296 | G > A     | 52  | 1.000 | ✓ HOM  |
| chr15:40185630 | G > A     | 54  | 1.000 | ✓ HOM  |
| chr15:40199751 | A > G     | 56  | 0.982 | ✓ HOM  |
| chr16:68823538 | T > C     | 42  | 1.000 | ✓ HOM  |
| chr19:10996457 | T > C     | 41  | 0.585 | ✓ HET  |
| chr19:11058220 | A > G     | 46  | 0.500 | ✓ HET  |
| chr22:20985799 | T > C     | 53  | 1.000 | ✓ HOM  |

All 6 invocations completed successfully in ~5 s, using ~640–680 MB of the 1024 MB allocation.

### Note on `[$LATEST]`

Log streams show `[$LATEST]` because the Lambda has no published version alias. This is consistent with other OrcaBus services (`service-workflow-manager`, `service-sash-pipeline-manager`) which also target `$LATEST` directly. Adopting Lambda aliases is a platform-wide decision.
