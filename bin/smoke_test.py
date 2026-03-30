#!/usr/bin/env python3
"""
Smoke-test the deployed extract-variant-af Lambda.

Steps:
  1. Crop a full DRAGEN hard-filtered VCF to the 10 monitoring sites.
  2. Upload the cropped VCF + tabix index to S3.
  3. Invoke the Lambda with a crafted WorkflowRunStateChange event.
  4. Print the response.

Usage:
  python3 bin/smoke_test.py /path/to/L2501484.hard-filtered.vcf.gz
  python3 bin/smoke_test.py /path/to/L2501484.hard-filtered.vcf.gz --stage beta
  python3 bin/smoke_test.py /path/to/L2501484.hard-filtered.vcf.gz --dry-run
  python3 bin/smoke_test.py --vcf-s3-uri s3://bucket/path/to/L2501116.hard-filtered.vcf.gz
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import boto3
import pysam

# ---- Config ----------------------------------------------------------------

FUNCTION_NAME_PREFIX = 'variant-monitoring-extract-variant-af'

BUCKET = 'pipeline-dev-cache-503977275616-ap-southeast-2'
PORTAL_RUN_ID = 'test20260330smoke1'
LIBRARY_ID = 'L2501484'
GERMLINE_REL = f'{LIBRARY_ID}__hg38__graph__dragen_wgts_dna_germline_variant_calling/'
VCF_PREFIX = (
    f'byob-icav2/development/analysis/dragen-wgts-dna/{PORTAL_RUN_ID}/{GERMLINE_REL}'
)
VCF_KEY = f'{VCF_PREFIX}{LIBRARY_ID}.hard-filtered.vcf.gz'
TBI_KEY = VCF_KEY + '.tbi'
OUTPUT_URI = f's3://{BUCKET}/byob-icav2/development/analysis/dragen-wgts-dna/{PORTAL_RUN_ID}/'

MONITORING_SITES = [
    ('chr2', 47803699),
    ('chr5', 112827157),
    ('chr11', 44130027),
    ('chr15', 40161296),
    ('chr15', 40185630),
    ('chr15', 40199751),
    ('chr16', 68823538),
    ('chr19', 10996457),
    ('chr19', 11058220),
    ('chr22', 20985799),
]


def _build_event(library_id: str, portal_run_id: str, output_uri: str, germline_rel: str) -> dict:
    return {
        'id': 'smoke-test-001',
        'source': 'orcabus.workflowmanager',
        'detail-type': 'WorkflowRunStateChange',
        'time': '2026-03-30T00:00:00Z',
        'account': '503977275616',
        'region': 'ap-southeast-2',
        'detail': {
            'id': 'wfr-smoketest',
            'orcabusId': 'wfr.smoketest',
            'portalRunId': portal_run_id,
            'workflowRunName': f'umccr--automated--dragen-wgts-dna--4-4-4--{portal_run_id}',
            'status': 'SUCCEEDED',
            'timestamp': '2026-03-30T00:00:00Z',
            'workflow': {
                'orcabusId': 'wfl.smoketest',
                'name': 'dragen-wgts-dna',
                'version': '4.4.4',
                'codeVersion': 'smoketest',
                'executionEngine': 'ICA',
            },
            'libraries': [
                {'orcabusId': 'lib.smoketest', 'libraryId': library_id},
            ],
            'payload': {
                'orcabusId': 'pld.smoketest',
                'refId': 'iwa.smoketest',
                'version': '2025.06.04',
                'data': {
                    'tags': {
                        'libraryId': library_id,
                        'subjectId': 'SBJ00000',
                        'individualId': 'NA12878',
                    },
                    'engineParameters': {
                        'outputUri': output_uri,
                        'logsUri': output_uri,
                        'projectId': 'smoketest',
                        'pipelineId': 'smoketest',
                    },
                    'outputs': {
                        'dragenGermlineVariantCallingOutputRelPath': germline_rel,
                    },
                },
            },
        },
    }


def _parse_vcf_s3_uri(s3_uri: str) -> tuple[str, str, str, str, str]:
    """Return (bucket, vcf_key, library_id, portal_run_id, germline_rel, output_uri)."""
    parsed = urlparse(s3_uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip('/')

    parts = key.split('/')
    vcf_name = parts[-1]
    library_id = vcf_name.replace('.hard-filtered.vcf.gz', '')
    germline_rel = parts[-2] + '/'

    try:
        wf_idx = parts.index('dragen-wgts-dna')
        portal_run_id = parts[wf_idx + 1]
        output_prefix = '/'.join(parts[:wf_idx + 2]) + '/'
    except ValueError:
        portal_run_id = parts[-3]
        output_prefix = '/'.join(parts[:-2]) + '/'

    output_uri = f's3://{bucket}/{output_prefix}'
    return bucket, key, library_id, portal_run_id, germline_rel, output_uri


# ---- VCF cropping ----------------------------------------------------------


def crop_vcf(source_vcf: Path, library_id: str, out_dir: Path) -> tuple[Path, Path]:
    """Extract monitoring site records from source_vcf into a bgzipped+indexed VCF."""
    plain = out_dir / f'{library_id}.hard-filtered.vcf'
    gz = out_dir / f'{library_id}.hard-filtered.vcf.gz'
    tbi = Path(str(gz) + '.tbi')

    with pysam.VariantFile(str(source_vcf)) as fh_in:
        with pysam.VariantFile(str(plain), 'w', header=fh_in.header) as fh_out:
            for chrom, pos in MONITORING_SITES:
                for record in fh_in.fetch(chrom, pos - 1, pos):
                    fh_out.write(record)

    pysam.tabix_compress(str(plain), str(gz), force=True)
    pysam.tabix_index(str(gz), preset='vcf', force=True)
    return gz, tbi


# ---- Main ------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description='Smoke-test the extract-variant-af Lambda.')
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        'source_vcf',
        nargs='?',
        type=Path,
        help='Path to a full DRAGEN hard-filtered VCF.gz (crop + upload mode)',
    )
    source_group.add_argument(
        '--vcf-s3-uri',
        help='Existing S3 URI of a hard-filtered VCF.gz (skip crop and upload)',
    )
    parser.add_argument(
        '--stage',
        default='beta',
        help='Deployment stage suffix appended to the function name (default: beta)',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Crop VCF only; skip S3 upload and Lambda invoke',
    )
    args = parser.parse_args()

    function_name = f'{FUNCTION_NAME_PREFIX}-{args.stage}'

    # ---- Mode: use existing S3 VCF -----------------------------------------
    if args.vcf_s3_uri:
        bucket, vcf_key, library_id, portal_run_id, germline_rel, output_uri = (
            _parse_vcf_s3_uri(args.vcf_s3_uri)
        )
        test_event = _build_event(library_id, portal_run_id, output_uri, germline_rel)

        print(f'Lambda:     {function_name}')
        print(f'VCF:        s3://{bucket}/{vcf_key}')
        print(f'outputUri:  {output_uri}')
        print(f'libraryId:  {library_id}')
        print(f'portalRunId:{portal_run_id}')

        if args.dry_run:
            print('\n[dry-run] Skipping Lambda invoke.')
            print(json.dumps(test_event, indent=2))
            return

        lam = boto3.client('lambda', region_name='ap-southeast-2')
        print('\nInvoking Lambda...')
        response = lam.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(test_event).encode(),
        )
        payload = json.loads(response['Payload'].read())
        print(f'HTTP status: {response["StatusCode"]}')
        if 'FunctionError' in response:
            print(f'FunctionError: {response["FunctionError"]}')
        print(json.dumps(payload, indent=2))
        return

    # ---- Mode: local VCF → crop → upload → invoke --------------------------
    if not args.source_vcf.exists():
        print(f'Error: {args.source_vcf} not found', file=sys.stderr)
        sys.exit(1)

    test_event = _build_event(LIBRARY_ID, PORTAL_RUN_ID, OUTPUT_URI, GERMLINE_REL)
    print(f'Lambda:  {function_name}')
    print(f'S3 key:  s3://{BUCKET}/{VCF_KEY}')

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        print('\nCropping VCF to 10 monitoring sites...')
        gz, tbi = crop_vcf(args.source_vcf, LIBRARY_ID, tmp_path)
        print(f'  {gz.name}      {gz.stat().st_size / 1024:.1f} KB')
        print(f'  {tbi.name}  {tbi.stat().st_size / 1024:.1f} KB')

        if args.dry_run:
            print('\n[dry-run] Skipping S3 upload and Lambda invoke.')
            return

        s3 = boto3.client('s3', region_name='ap-southeast-2')
        print('\nUploading to S3...')
        s3.upload_file(str(gz), BUCKET, VCF_KEY)
        s3.upload_file(str(tbi), BUCKET, TBI_KEY)
        print('  done')

        lam = boto3.client('lambda', region_name='ap-southeast-2')
        print('\nInvoking Lambda...')
        response = lam.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(test_event).encode(),
        )

        payload = json.loads(response['Payload'].read())
        status = response['StatusCode']
        print(f'HTTP status: {status}')
        if 'FunctionError' in response:
            print(f'FunctionError: {response["FunctionError"]}')
        print(json.dumps(payload, indent=2))


if __name__ == '__main__':
    main()
