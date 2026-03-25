import pytest
import pysam


@pytest.fixture
def sample_icav2_wes_event():
    """A complete EventBridge Icav2WesAnalysisStateChange event for dragen-wgts-dna."""
    return {
        'id': 'abc-123',
        'source': 'orcabus.icav2wesmanager',
        'detail-type': 'Icav2WesAnalysisStateChange',
        'time': '2026-03-12T00:00:00Z',
        'account': '123456789012',
        'region': 'ap-southeast-2',
        'detail': {
            'id': 'analysis-01ABC123',
            'name': 'orca--dragen-wgts-dna--20260312abcd1234',
            'status': 'SUCCEEDED',
            'tags': {
                'portalRunId': '20260312abcd1234',  # pragma: allowlist secret
                'libraryId': 'L2600148',
                'subjectId': 'SBJ00027',
                'individualId': 'NA12878',
            },
            'inputs': {},
            'engineParameters': {
                'outputUri': 's3://test-cache-bucket/byob-icav2/development/analysis/dragen-wgts-dna/20260312abcd1234/',
            },
        },
    }


@pytest.fixture
def sample_icav2_wes_event_running(sample_icav2_wes_event):
    """Same event but with RUNNING status."""
    event = dict(sample_icav2_wes_event)
    event['detail'] = {**sample_icav2_wes_event['detail'], 'status': 'RUNNING'}
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
    """Bgzipped + tabix-indexed DRAGEN hard-filtered VCF with one variant.

    chr5:112827157 T>C  DP=30, AF=0.5, FILTER=PASS
    Used to test extract_af_at_site and extract_all_sites against real VCF data.
    """
    vcf_content = (
        '##fileformat=VCFv4.2\n'
        '##FILTER=<ID=PASS,Description="All filters passed">\n'
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
        '##FORMAT=<ID=AF,Number=A,Type=Float,Description="Allele frequency">\n'
        '##contig=<ID=chr5,length=181538259>\n'
        '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tL2600148\n'
        'chr5\t112827157\t.\tT\tC\t.\tPASS\t.\tGT:DP:AF\t0/1:30:0.5\n'
    )
    plain_path = tmp_path / 'L2600148.hard-filtered.vcf'
    plain_path.write_text(vcf_content)

    gz_path = str(plain_path) + '.gz'
    pysam.tabix_compress(str(plain_path), gz_path, force=True)
    pysam.tabix_index(gz_path, preset='vcf', force=True)

    return tmp_path / 'L2600148.hard-filtered.vcf.gz'
