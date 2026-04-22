"""
Tests for the variant monitoring Lambda handler.

Uses moto to mock S3 and EventBridge. VCF extraction tests use real
bgzipped + tabix-indexed VCF files built by conftest fixtures.
"""
import json
import os
from datetime import datetime, timezone
from unittest.mock import patch

import pysam
import pytest
from moto import mock_aws

os.environ.setdefault('EVENT_BUS_NAME', 'OrcaBusMain')
os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-southeast-2')


PORTAL_RUN_ID = '20260312abcd1234'  # pragma: allowlist secret
BUCKET = 'test-cache-bucket'
GERMLINE_REL_PATH = 'L2600148__hg38__graph__dragen_wgts_dna_germline_variant_calling/'
VCF_KEY = (
    f'byob-icav2/development/analysis/dragen-wgts-dna/{PORTAL_RUN_ID}/'
    f'{GERMLINE_REL_PATH}L2600148.hard-filtered.vcf.gz'
)
TBI_KEY = VCF_KEY + '.tbi'
OUTPUT_URI = f's3://{BUCKET}/byob-icav2/development/analysis/dragen-wgts-dna/{PORTAL_RUN_ID}/'


def _make_mock_site_results():
    """Return MonitoringSiteResult objects for all 10 monitoring sites."""
    from variant_monitoring.models import MonitoringSiteResult

    sites = [
        ('chr2', 47803699, 'A', 'T'),
        ('chr5', 112827157, 'T', 'C'),
        ('chr11', 44130027, 'T', 'A'),
        ('chr15', 40161296, 'G', 'A'),
        ('chr15', 40185630, 'G', 'A'),
        ('chr15', 40199751, 'A', 'G'),
        ('chr16', 68823538, 'T', 'C'),
        ('chr19', 10996457, 'T', 'C'),
        ('chr19', 11058220, 'A', 'G'),
        ('chr22', 20985799, 'T', 'C'),
    ]
    return [
        MonitoringSiteResult(
            chrom=chrom,
            pos=pos,
            ref=ref,
            alt=alt,
            dp=25,
            af=0.5,
            filter_status='PASS',
            variant_emitted=True,
        )
        for chrom, pos, ref, alt in sites
    ]


@mock_aws
class TestHandler:
    """Integration-style tests for lambda_handler (S3 + EventBridge mocked)."""

    def _setup_s3(self):
        from tests.conftest import _local_session
        s3 = _local_session().client('s3')
        s3.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={'LocationConstraint': 'ap-southeast-2'},
        )
        s3.put_object(Bucket=BUCKET, Key=VCF_KEY, Body=b'fake-vcf-content')
        s3.put_object(Bucket=BUCKET, Key=TBI_KEY, Body=b'fake-tbi-content')

    def _setup_events(self):
        from tests.conftest import _local_session
        eb = _local_session().client('events')
        eb.create_event_bus(Name='OrcaBusMain')

    def test_handler_skips_multiple_libraries(self, sample_wrsc_event):
        from variant_monitoring.lambdas.extract_variant_af import lambda_handler

        # Real production dragen-wgts-dna event (portalRunId=20260408604c7e56) with 2 libraries
        event = dict(sample_wrsc_event)
        event['detail'] = {
            **sample_wrsc_event['detail'],
            'libraries': [
                {'orcabusId': 'lib.01JBMTS8YCE48WBFRYH18ACNWS', 'libraryId': 'L2101472'},
                {'orcabusId': 'lib.01JBMTS8X2BXR3PZHH9X2GHFK8', 'libraryId': 'L2101471'},
            ],
        }

        result = lambda_handler(event, None)

        assert result['statusCode'] == 200
        assert result['skipped'] is True
        assert 'libraries=2' in result['reason']

    def test_handler_skips_zero_libraries(self, sample_wrsc_event):
        from variant_monitoring.lambdas.extract_variant_af import lambda_handler

        event = dict(sample_wrsc_event)
        event['detail'] = {**sample_wrsc_event['detail'], 'libraries': []}

        result = lambda_handler(event, None)

        assert result['statusCode'] == 200
        assert result['skipped'] is True
        assert 'libraries=0' in result['reason']

    def test_handler_skips_non_batch_control(self, sample_wrsc_event):
        from variant_monitoring.lambdas.extract_variant_af import lambda_handler

        event = dict(sample_wrsc_event)
        detail = dict(sample_wrsc_event['detail'])
        payload = dict(sample_wrsc_event['detail']['payload'])
        data = dict(sample_wrsc_event['detail']['payload']['data'])
        data['tags'] = {**data['tags'], 'individualId': 'NA99999'}
        payload['data'] = data
        detail['payload'] = payload
        event['detail'] = detail

        result = lambda_handler(event, None)

        assert result['statusCode'] == 200
        assert result['skipped'] is True
        assert 'not a GIAB batch control' in result['reason']

    def test_handler_skips_non_succeeded(self, sample_wrsc_event_running):
        from variant_monitoring.lambdas.extract_variant_af import lambda_handler

        result = lambda_handler(sample_wrsc_event_running, None)

        assert result['statusCode'] == 200
        assert result['skipped'] is True
        assert 'RUNNING' in result['reason']

    def test_handler_success(self, sample_wrsc_event):
        self._setup_s3()
        self._setup_events()

        mock_results = _make_mock_site_results()

        with (
            patch(
                'variant_monitoring.lambdas.extract_variant_af.extract_all_sites',
                return_value=mock_results,
            ),
            patch(
                'variant_monitoring.lambdas.extract_variant_af.events_client'
            ) as mock_eb,
        ):
            mock_eb.put_events.return_value = {
                'FailedEntryCount': 0,
                'Entries': [{'EventId': 'mock-event-id-001'}],
            }

            from variant_monitoring.lambdas.extract_variant_af import lambda_handler

            result = lambda_handler(sample_wrsc_event, None)

        assert result['statusCode'] == 200
        assert result['eventId'] == 'mock-event-id-001'

    def test_handler_emits_correct_event_metadata(self, sample_wrsc_event):
        self._setup_s3()
        self._setup_events()

        mock_results = _make_mock_site_results()

        with (
            patch(
                'variant_monitoring.lambdas.extract_variant_af.extract_all_sites',
                return_value=mock_results,
            ),
            patch(
                'variant_monitoring.lambdas.extract_variant_af.events_client'
            ) as mock_eb,
        ):
            mock_eb.put_events.return_value = {
                'FailedEntryCount': 0,
                'Entries': [{'EventId': 'mock-event-id-002'}],
            }

            from variant_monitoring.lambdas.extract_variant_af import lambda_handler

            lambda_handler(sample_wrsc_event, None)

        entry = mock_eb.put_events.call_args.kwargs['Entries'][0]
        assert entry['Source'] == 'orcabus.variantmonitoring'
        assert entry['DetailType'] == 'VariantMonitoringResult'
        assert entry['EventBusName'] == 'OrcaBusMain'

    def test_handler_detail_contains_expected_fields(self, sample_wrsc_event):
        self._setup_s3()
        self._setup_events()

        mock_results = _make_mock_site_results()

        with (
            patch(
                'variant_monitoring.lambdas.extract_variant_af.extract_all_sites',
                return_value=mock_results,
            ),
            patch(
                'variant_monitoring.lambdas.extract_variant_af.events_client'
            ) as mock_eb,
        ):
            mock_eb.put_events.return_value = {
                'FailedEntryCount': 0,
                'Entries': [{'EventId': 'mock-event-id-003'}],
            }

            from variant_monitoring.lambdas.extract_variant_af import lambda_handler

            lambda_handler(sample_wrsc_event, None)

        entry = mock_eb.put_events.call_args.kwargs['Entries'][0]
        detail = json.loads(entry['Detail'])
        assert detail['portalRunId'] == PORTAL_RUN_ID
        assert detail['libraryId'] == 'L2600148'
        assert detail['subjectId'] == 'SBJ00027'
        assert detail['individualId'] == 'NA12878'
        assert detail['giabId'] == 'HG001'
        assert detail['outputUri'] == OUTPUT_URI
        assert detail['analysisName'] == 'umccr--automated--dragen-wgts-dna--4-4-4--20260312abcd1234'
        assert len(detail['monitoringSites']) == 10

    def test_handler_raises_on_eventbridge_failure(self, sample_wrsc_event):
        self._setup_s3()

        mock_results = _make_mock_site_results()

        with (
            patch(
                'variant_monitoring.lambdas.extract_variant_af.extract_all_sites',
                return_value=mock_results,
            ),
            patch(
                'variant_monitoring.lambdas.extract_variant_af.events_client'
            ) as mock_eb,
        ):
            mock_eb.put_events.return_value = {
                'FailedEntryCount': 1,
                'Entries': [{'ErrorCode': 'ThrottlingException', 'ErrorMessage': 'Rate exceeded'}],
            }

            from variant_monitoring.lambdas.extract_variant_af import lambda_handler

            with pytest.raises(RuntimeError, match='EventBridge put_events failed'):
                lambda_handler(sample_wrsc_event, None)

    def test_handler_raises_when_vcf_not_found(self, sample_wrsc_event):
        """Handler raises FileNotFoundError when VCF is absent from S3."""
        from tests.conftest import _local_session
        s3 = _local_session().client('s3')
        s3.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={'LocationConstraint': 'ap-southeast-2'},
        )

        from variant_monitoring.lambdas.extract_variant_af import lambda_handler

        with pytest.raises(FileNotFoundError):
            lambda_handler(sample_wrsc_event, None)

    def test_handler_raises_when_output_uri_missing(self, sample_wrsc_event):
        """Handler raises ValueError when payload.data.engineParameters.outputUri is absent."""
        event = dict(sample_wrsc_event)
        event['detail'] = {
            **sample_wrsc_event['detail'],
            'payload': {
                'orcabusId': 'pld.test',
                'version': '2025.06.04',
                'data': {
                    'tags': {'libraryId': 'L2600148', 'subjectId': 'SBJ00027', 'individualId': 'NA12878'},
                    'engineParameters': {},
                    'outputs': {'dragenGermlineVariantCallingOutputRelPath': 'germline/'},
                },
            },
        }

        from variant_monitoring.lambdas.extract_variant_af import lambda_handler

        with pytest.raises(ValueError, match='outputUri missing'):
            lambda_handler(event, None)

    def test_handler_uses_germline_output_path_when_present(self, sample_wrsc_event):
        """find_hard_filtered_vcf is called with the narrowed germline subdirectory URI."""
        from variant_monitoring.lambdas.extract_variant_af import lambda_handler

        germline_rel = 'L2600148__hg38__graph__dragen_wgts_dna_germline_variant_calling/'
        expected_vcf_uri = OUTPUT_URI.rstrip('/') + '/' + germline_rel

        with patch(
            'variant_monitoring.lambdas.extract_variant_af.find_hard_filtered_vcf',
            side_effect=FileNotFoundError('stop after uri check'),
        ) as mock_find:
            with pytest.raises(FileNotFoundError):
                lambda_handler(sample_wrsc_event, None)

        mock_find.assert_called_once_with(expected_vcf_uri)

    def test_handler_falls_back_to_output_uri_without_germline_path(self, sample_wrsc_event):
        """find_hard_filtered_vcf uses full outputUri when germline path is absent."""
        from variant_monitoring.lambdas.extract_variant_af import lambda_handler

        event = dict(sample_wrsc_event)
        detail = dict(sample_wrsc_event['detail'])
        payload = dict(sample_wrsc_event['detail']['payload'])
        data = dict(sample_wrsc_event['detail']['payload']['data'])
        data['outputs'] = {}
        payload['data'] = data
        detail['payload'] = payload
        event['detail'] = detail

        with patch(
            'variant_monitoring.lambdas.extract_variant_af.find_hard_filtered_vcf',
            side_effect=FileNotFoundError('stop after uri check'),
        ) as mock_find:
            with pytest.raises(FileNotFoundError):
                lambda_handler(event, None)

        mock_find.assert_called_once_with(OUTPUT_URI)


class TestModels:
    """Pydantic model parsing tests."""

    def test_incoming_event_parses_detail_type(self, sample_wrsc_event):
        from variant_monitoring.models import IncomingEvent

        event = IncomingEvent.model_validate(sample_wrsc_event)
        assert event.detail_type == 'WorkflowRunStateChange'

    def test_incoming_event_parses_portal_run_id(self, sample_wrsc_event):
        from variant_monitoring.models import IncomingEvent

        event = IncomingEvent.model_validate(sample_wrsc_event)
        assert event.detail.portalRunId == PORTAL_RUN_ID

    def test_incoming_event_parses_library_from_payload_tags(self, sample_wrsc_event):
        from variant_monitoring.models import IncomingEvent

        event = IncomingEvent.model_validate(sample_wrsc_event)
        assert event.detail.payload.data.tags.libraryId == 'L2600148'

    def test_incoming_event_parses_status(self, sample_wrsc_event):
        from variant_monitoring.models import IncomingEvent

        event = IncomingEvent.model_validate(sample_wrsc_event)
        assert event.detail.status == 'SUCCEEDED'

    def test_incoming_event_parses_output_uri(self, sample_wrsc_event):
        from variant_monitoring.models import IncomingEvent

        event = IncomingEvent.model_validate(sample_wrsc_event)
        assert event.detail.payload.data.engineParameters.outputUri == OUTPUT_URI

    def test_result_detail_serializes_monitoring_sites(self):
        from variant_monitoring.models import MonitoringSiteResult, VariantMonitoringResultDetail

        detail = VariantMonitoringResultDetail(
            id='abc123',
            version='0.1.0',
            timestamp=datetime.now(tz=timezone.utc),
            portalRunId=PORTAL_RUN_ID,
            libraryId='L2600148',
            subjectId='SBJ00027',
            individualId='NA12878',
            analysisName='orca--dragen-wgts-dna--20260312abcd1234',
            outputUri=OUTPUT_URI,
            monitoringSites=[
                MonitoringSiteResult(
                    chrom='chr5',
                    pos=112827157,
                    ref='T',
                    alt='C',
                    dp=21,
                    af=0.476,
                    filter_status='PASS',
                    variant_emitted=True,
                )
            ],
        )
        parsed = json.loads(detail.model_dump_json())
        assert parsed['portalRunId'] == PORTAL_RUN_ID
        assert len(parsed['monitoringSites']) == 1
        assert parsed['monitoringSites'][0]['af'] == pytest.approx(0.476, abs=1e-6)


class TestFindVcf:  # noqa: D101
    """Unit tests for S3 VCF discovery."""

    @mock_aws
    def test_find_hard_filtered_vcf_tbi_missing(self):
        """Returns normally when VCF exists but tabix index is absent — fallback to local indexing."""
        from tests.conftest import _local_session

        s3 = _local_session().client('s3')
        s3.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={'LocationConstraint': 'ap-southeast-2'},
        )
        s3.put_object(Bucket=BUCKET, Key=VCF_KEY, Body=b'')
        # intentionally NOT uploading TBI_KEY

        from variant_monitoring.lambdas.extract_variant_af import find_hard_filtered_vcf

        bucket, vcf_key, tbi_key = find_hard_filtered_vcf(OUTPUT_URI)
        assert bucket == BUCKET
        assert vcf_key == VCF_KEY
        assert tbi_key == TBI_KEY

    @mock_aws
    def test_find_hard_filtered_vcf_success(self):
        from tests.conftest import _local_session
        s3 = _local_session().client('s3')
        s3.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={'LocationConstraint': 'ap-southeast-2'},
        )
        s3.put_object(Bucket=BUCKET, Key=VCF_KEY, Body=b'')
        s3.put_object(Bucket=BUCKET, Key=TBI_KEY, Body=b'')

        from variant_monitoring.lambdas.extract_variant_af import find_hard_filtered_vcf

        bucket, vcf_key, tbi_key = find_hard_filtered_vcf(OUTPUT_URI)
        assert bucket == BUCKET
        assert vcf_key == VCF_KEY
        assert tbi_key == TBI_KEY

    @mock_aws
    def test_find_hard_filtered_vcf_not_found(self):
        from tests.conftest import _local_session
        s3 = _local_session().client('s3')
        s3.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={'LocationConstraint': 'ap-southeast-2'},
        )

        from variant_monitoring.lambdas.extract_variant_af import find_hard_filtered_vcf

        with pytest.raises(FileNotFoundError):
            find_hard_filtered_vcf(OUTPUT_URI)


class TestLoadMonitoringSites:
    """Unit tests for load_monitoring_sites."""

    def test_loads_sites_from_vcf(self, monitoring_sites_vcf):
        from variant_monitoring.lambdas.extract_variant_af import load_monitoring_sites

        sites = load_monitoring_sites(monitoring_sites_vcf)

        assert len(sites) == 1
        assert sites[0] == ('chr5', 112827157, 'T', 'C')

    def test_loads_sites_no_alts(self, tmp_path):
        """Record with no ALT allele (monomorphic site) gets alt='.'."""
        vcf_content = (
            '##fileformat=VCFv4.2\n'
            '##contig=<ID=chr5,length=181538259>\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
            'chr5\t112827157\t.\tT\t.\t.\t.\t.\n'
        )
        vcf_path = tmp_path / 'no_alt.vcf'
        vcf_path.write_text(vcf_content)

        from variant_monitoring.lambdas.extract_variant_af import load_monitoring_sites

        sites = load_monitoring_sites(vcf_path)

        assert len(sites) == 1
        assert sites[0] == ('chr5', 112827157, 'T', '.')

    def test_raises_on_empty_vcf(self, tmp_path):
        vcf_path = tmp_path / 'empty.vcf'
        vcf_path.write_text(
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
        )

        from variant_monitoring.lambdas.extract_variant_af import load_monitoring_sites

        with pytest.raises(ValueError, match='No monitoring sites found'):
            load_monitoring_sites(vcf_path)


class TestExtractAllSites:
    """Unit tests for pysam-based AF extraction using real VCF data."""

    def test_extract_af_at_site_found(self, dragen_vcf):
        """Returns correct DP/AF/filter when the variant exists in the VCF."""
        from variant_monitoring.lambdas.extract_variant_af import extract_af_at_site

        with pysam.VariantFile(str(dragen_vcf)) as vcf_fh:
            result = extract_af_at_site(vcf_fh, 'chr5', 112827157, 'T', 'C')

        assert result.variant_emitted is True
        assert result.dp == 30
        assert result.af == pytest.approx(0.5)
        assert result.filter_status == 'PASS'

    def test_extract_af_at_site_not_emitted(self, dragen_vcf):
        """Returns variant_emitted=False when position is absent from the VCF."""
        from variant_monitoring.lambdas.extract_variant_af import extract_af_at_site

        with pysam.VariantFile(str(dragen_vcf)) as vcf_fh:
            result = extract_af_at_site(vcf_fh, 'chr22', 20985799, 'T', 'C')

        assert result.variant_emitted is False
        assert result.dp == 0
        assert result.af == pytest.approx(0.0)
        assert result.filter_status == '.'

    def test_extract_all_sites(self, dragen_vcf, monitoring_sites_vcf):
        """extract_all_sites returns one result per site in the regions VCF."""
        from variant_monitoring.lambdas.extract_variant_af import extract_all_sites

        results = extract_all_sites(dragen_vcf, regions_fp=monitoring_sites_vcf)

        assert len(results) == 1
        assert results[0].chrom == 'chr5'
        assert results[0].pos == 112827157
        assert results[0].variant_emitted is True
        assert results[0].dp == 30
        assert results[0].af == pytest.approx(0.5)

    def test_extract_af_at_site_filtered(self, dragen_vcf):
        """Filtered variant is returned with variant_emitted=True and correct filter_status."""
        from variant_monitoring.lambdas.extract_variant_af import extract_af_at_site

        with pysam.VariantFile(str(dragen_vcf)) as vcf_fh:
            result = extract_af_at_site(vcf_fh, 'chr2', 47803699, 'A', 'T')

        assert result.variant_emitted is True
        assert result.dp == 20
        assert result.af == pytest.approx(0.3)
        assert result.filter_status == 'LowQual'

    def test_extract_af_at_site_allele_mismatch(self, dragen_vcf):
        """Position present in VCF but with a different alt; returns variant_emitted=False."""
        from variant_monitoring.lambdas.extract_variant_af import extract_af_at_site

        with pysam.VariantFile(str(dragen_vcf)) as vcf_fh:
            result = extract_af_at_site(vcf_fh, 'chr22', 20985799, 'T', 'C')  # VCF has T>G

        assert result.variant_emitted is False
        assert result.dp == 0
        assert result.af == pytest.approx(0.0)
        assert result.filter_status == '.'

    def test_extract_af_at_site_wrong_pos_skipped(self, tmp_path):
        """Deletion overlapping the query window but at a different pos is skipped; returns variant_emitted=False."""
        # Deletion at chr5:112827156 spans 0-based [112827155, 112827157),
        # which overlaps the fetch window for pos=112827157 → returned by tabix
        # but record.pos (112827156) != 112827157 → continue.
        vcf_content = (
            '##fileformat=VCFv4.2\n'
            '##FILTER=<ID=PASS,Description="All filters passed">\n'
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
            '##FORMAT=<ID=AF,Number=A,Type=Float,Description="Allele frequency">\n'
            '##contig=<ID=chr5,length=181538259>\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n'
            'chr5\t112827156\t.\tTC\tT\t.\tPASS\t.\tGT:DP:AF\t0/1:30:0.5\n'
        )
        plain = tmp_path / 'wrong_pos.vcf'
        plain.write_text(vcf_content)
        gz = str(plain) + '.gz'
        pysam.tabix_compress(str(plain), gz, force=True)
        pysam.tabix_index(gz, preset='vcf', force=True)

        from variant_monitoring.lambdas.extract_variant_af import extract_af_at_site

        with pysam.VariantFile(gz) as vcf_fh:
            result = extract_af_at_site(vcf_fh, 'chr5', 112827157, 'T', 'C')

        assert result.variant_emitted is False

    def test_extract_af_at_site_wrong_ref_skipped(self, tmp_path):
        """Record at correct pos but wrong ref is skipped; returns variant_emitted=False."""
        vcf_content = (
            '##fileformat=VCFv4.2\n'
            '##FILTER=<ID=PASS,Description="All filters passed">\n'
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
            '##FORMAT=<ID=AF,Number=A,Type=Float,Description="Allele frequency">\n'
            '##contig=<ID=chr5,length=181538259>\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n'
            'chr5\t112827157\t.\tG\tC\t.\tPASS\t.\tGT:DP:AF\t0/1:30:0.5\n'
        )
        plain = tmp_path / 'wrong_ref.vcf'
        plain.write_text(vcf_content)
        gz = str(plain) + '.gz'
        pysam.tabix_compress(str(plain), gz, force=True)
        pysam.tabix_index(gz, preset='vcf', force=True)

        from variant_monitoring.lambdas.extract_variant_af import extract_af_at_site

        with pysam.VariantFile(gz) as vcf_fh:
            result = extract_af_at_site(vcf_fh, 'chr5', 112827157, 'T', 'C')

        assert result.variant_emitted is False

    def test_extract_af_at_site_pysam_exception_returns_not_emitted(self):
        """pysam exception during fetch is caught; site returns variant_emitted=False without crashing."""
        from unittest.mock import MagicMock

        from variant_monitoring.lambdas.extract_variant_af import extract_af_at_site

        mock_vcf = MagicMock()
        mock_vcf.fetch.side_effect = Exception('pysam internal error')

        result = extract_af_at_site(mock_vcf, 'chr5', 112827157, 'T', 'C')

        assert result.variant_emitted is False
        assert result.dp == 0
        assert result.af == pytest.approx(0.0)

    def test_extract_all_sites_uses_bundled_vcf_when_no_regions_fp(self, dragen_vcf):
        """extract_all_sites uses the bundled varmon_10_sites.vcf when regions_fp is omitted."""
        from variant_monitoring.lambdas.extract_variant_af import extract_all_sites

        results = extract_all_sites(dragen_vcf)

        assert len(results) == 10

    def test_extract_all_sites_mixed(self, dragen_vcf, monitoring_sites_vcf_multi):
        """extract_all_sites handles a mix of PASS, filtered, and allele-mismatch sites."""
        from variant_monitoring.lambdas.extract_variant_af import extract_all_sites

        results = extract_all_sites(dragen_vcf, regions_fp=monitoring_sites_vcf_multi)

        assert len(results) == 3
        by_chrom = {r.chrom: r for r in results}

        assert by_chrom['chr2'].variant_emitted is True
        assert by_chrom['chr2'].filter_status == 'LowQual'

        assert by_chrom['chr5'].variant_emitted is True
        assert by_chrom['chr5'].filter_status == 'PASS'

        assert by_chrom['chr22'].variant_emitted is False
        assert by_chrom['chr22'].filter_status == '.'
