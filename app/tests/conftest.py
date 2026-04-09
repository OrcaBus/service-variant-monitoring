import boto3
import pytest
import pysam
from unittest.mock import patch


def _local_session() -> boto3.Session:
    """Return a boto3 Session with static test credentials (no SSO, no refresh)."""
    return boto3.Session(
        aws_access_key_id='testing',  # pragma: allowlist secret
        aws_secret_access_key='testing',  # pragma: allowlist secret
        aws_session_token='testing',  # pragma: allowlist secret
        region_name='ap-southeast-2',
    )


@pytest.fixture(autouse=True)
def _patch_lambda_aws_clients():
    """Patch module-level boto3 clients in the Lambda with static-credential ones.

    Prevents real SSO/IAM credential resolution during tests. moto still
    intercepts all HTTP calls when @mock_aws is active.
    """
    session = _local_session()
    with patch(
        'variant_monitoring.lambdas.extract_variant_af.s3_client',
        session.client('s3', region_name='ap-southeast-2'),
    ), patch(
        'variant_monitoring.lambdas.extract_variant_af.events_client',
        session.client('events', region_name='ap-southeast-2'),
    ):
        yield


@pytest.fixture
def sample_wrsc_event():
    """A complete EventBridge WorkflowRunStateChange SUCCEEDED event for dragen-wgts-dna.

    Payload structure mirrors a real event captured from the
    orca-dragen-wgts-dna--icav2WesEventToWrscEvent Step Function output (2026-03-16).
    """
    return {
        'id': 'eb-abc-123',
        'source': 'orcabus.workflowmanager',
        'detail-type': 'WorkflowRunStateChange',
        'time': '2026-03-12T00:00:00Z',
        'account': '123456789012',
        'region': 'ap-southeast-2',
        'detail': {
            'id': 'wfr-01ABC123',
            'orcabusId': 'wfr.01JKABCDEFGHIJKL',
            'portalRunId': '20260312abcd1234',  # pragma: allowlist secret
            'workflowRunName': 'umccr--automated--dragen-wgts-dna--4-4-4--20260312abcd1234',
            'status': 'SUCCEEDED',
            'timestamp': '2026-03-12T00:00:00Z',
            'workflow': {
                'orcabusId': 'wfl.01KE5Q8Y35S9MHFFWRGNPKPRYW',
                'name': 'dragen-wgts-dna',
                'version': '4.4.4',
                'codeVersion': '724101a',
                'executionEngine': 'ICA',
            },
            'libraries': [
                {'orcabusId': 'lib.01JBMVHMS2NTF2M71F5M2H89SJ', 'libraryId': 'L2600148'},
            ],
            'payload': {
                'orcabusId': 'pld.01JKABCDEFGHIJKL',
                'refId': 'iwa.01KKT075HJC15W3FQX4AHGA9MG',
                'version': '2025.06.04',
                'data': {
                    'tags': {
                        'libraryId': 'L2600148',
                        'subjectId': 'SBJ00027',
                        'individualId': 'NA12878',
                    },
                    'engineParameters': {
                        'outputUri': 's3://test-cache-bucket/byob-icav2/development/analysis/dragen-wgts-dna/20260312abcd1234/',
                        'logsUri': 's3://test-cache-bucket/byob-icav2/development/logs/dragen-wgts-dna/20260312abcd1234/',
                        'projectId': 'ea19a3f5-ec7c-4940-a474-c31cd91dbad4',
                        'pipelineId': '812c4ee5-b0bd-4c55-b4c2-cafe70ecfc8e',
                    },
                    'outputs': {
                        'dragenGermlineVariantCallingOutputRelPath': 'L2600148__hg38__graph__dragen_wgts_dna_germline_variant_calling/',
                    },
                },
            },
        },
    }


@pytest.fixture
def sample_wrsc_event_running(sample_wrsc_event):
    """Same event but with RUNNING status."""
    event = dict(sample_wrsc_event)
    event['detail'] = {**sample_wrsc_event['detail'], 'status': 'RUNNING'}
    return event


@pytest.fixture
def monitoring_sites_vcf(tmp_path):
    """Plain VCF with a single monitoring site (chr5:112827157 T>C).

    No bgzip/tabix needed — load_monitoring_sites iterates sequentially.
    """
    vcf_content = (
        '##fileformat=VCFv4.2\n'
        '##FILTER=<ID=PASS,Description="All filters passed">\n'
        '##contig=<ID=chr5,length=181538259>\n'
        '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
        'chr5\t112827157\t.\tT\tC\t.\tPASS\t.\n'
    )
    vcf_path = tmp_path / 'regions.vcf'
    vcf_path.write_text(vcf_content)
    return vcf_path


@pytest.fixture
def dragen_vcf(tmp_path):
    """Bgzipped + tabix-indexed DRAGEN hard-filtered VCF with three variants.

    chr2:47803699  A>T  DP=20, AF=0.3,  FILTER=LowQual  (filtered but emitted)
    chr5:112827157 T>C  DP=30, AF=0.5,  FILTER=PASS     (clean variant)
    chr22:20985799 T>G  DP=25, AF=0.48, FILTER=PASS     (wrong alt — site expects T>C)
    """
    vcf_content = (
        '##fileformat=VCFv4.2\n'
        '##FILTER=<ID=PASS,Description="All filters passed">\n'
        '##FILTER=<ID=LowQual,Description="Low quality">\n'
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
        '##FORMAT=<ID=AF,Number=A,Type=Float,Description="Allele frequency">\n'
        '##contig=<ID=chr2,length=242193529>\n'
        '##contig=<ID=chr5,length=181538259>\n'
        '##contig=<ID=chr22,length=50818468>\n'
        '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tL2600148\n'
        'chr2\t47803699\t.\tA\tT\t.\tLowQual\t.\tGT:DP:AF\t0/1:20:0.3\n'
        'chr5\t112827157\t.\tT\tC\t.\tPASS\t.\tGT:DP:AF\t0/1:30:0.5\n'
        'chr22\t20985799\t.\tT\tG\t.\tPASS\t.\tGT:DP:AF\t0/1:25:0.48\n'
    )
    plain_path = tmp_path / 'L2600148.hard-filtered.vcf'
    plain_path.write_text(vcf_content)

    gz_path = str(plain_path) + '.gz'
    pysam.tabix_compress(str(plain_path), gz_path, force=True)
    pysam.tabix_index(gz_path, preset='vcf', force=True)

    return tmp_path / 'L2600148.hard-filtered.vcf.gz'


@pytest.fixture
def monitoring_sites_vcf_multi(tmp_path):
    """VCF with three monitoring sites for mixed found/filtered/not-found testing.

    chr2:47803699  A>T  — filtered variant in dragen_vcf
    chr5:112827157 T>C  — PASS variant in dragen_vcf
    chr22:20985799 T>C  — allele mismatch (dragen_vcf has T>G)
    """
    vcf_content = (
        '##fileformat=VCFv4.2\n'
        '##contig=<ID=chr2,length=242193529>\n'
        '##contig=<ID=chr5,length=181538259>\n'
        '##contig=<ID=chr22,length=50818468>\n'
        '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
        'chr2\t47803699\t.\tA\tT\t.\t.\t.\n'
        'chr5\t112827157\t.\tT\tC\t.\t.\t.\n'
        'chr22\t20985799\t.\tT\tC\t.\t.\t.\n'
    )
    vcf_path = tmp_path / 'regions_multi.vcf'
    vcf_path.write_text(vcf_content)
    return vcf_path
