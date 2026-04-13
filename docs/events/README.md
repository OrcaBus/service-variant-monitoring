# Event schemas

Events emitted by the Variant Monitoring service. Each event lives in its own
directory alongside usage examples.

## Events

### VariantMonitoringResult

Emitted after allele-frequency extraction completes for a DRAGEN hard-filtered
VCF. One event per SUCCEEDED `WorkflowRunStateChange` for `dragen-wgts-dna`.

| Producer | Consumers | Event Bus | Schema |
|---|---|---|---|
| `orcabus.variantmonitoring` | Downstream QC / reporting services | OrcaBusMain | [VariantMonitoringResult.schema.json](VariantMonitoringResult/VariantMonitoringResult.schema.json) |

See [examples/](VariantMonitoringResult/examples/) for a full example payload.

## JSON validation

Example events can be validated against their JSON schema using the
[`json` CLI](https://github.com/nicois/json):

```bash
json validate \
  --schema-file=VariantMonitoringResult/VariantMonitoringResult.schema.json \
  --document-file=VariantMonitoringResult/examples/VMR__example1.json
```
