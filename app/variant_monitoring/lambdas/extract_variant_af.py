"""
Variant monitoring Lambda handler.

Triggered by WorkflowRunStateChange (SUCCEEDED) events for dragen-wgts-dna
analyses. Downloads the DRAGEN hard-filtered VCF from S3, queries germline
variant monitoring sites with pysam/tabix, then emits a VariantMonitoringResult
event to the OrcaBus EventBridge bus.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast
from urllib.parse import urlparse

import boto3
import pysam

from variant_monitoring.models import (
    IncomingEvent,
    MonitoringSiteResult,
    VariantMonitoringResultDetail,
    WorkflowRunStateChangeDetail,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---- Clients (initialised outside handler for connection reuse) ----
events_client = boto3.client('events')
s3_client = boto3.client('s3')

# ---- Environment ----
EVENT_BUS_NAME = os.environ.get('EVENT_BUS_NAME', 'OrcaBusMain')

# ---- Constants ----
EVENT_SOURCE = 'orcabus.variantmonitoring'
EVENT_DETAIL_TYPE = 'VariantMonitoringResult'
SUCCEEDED_STATUS = 'SUCCEEDED'

# GIAB identifier mapping for known positive-control cell lines (NATA accreditation)
INDIVIDUAL_ID_TO_GIAB_ID: Dict[str, str] = {
    'NA12878': 'HG001',
    'NA24385': 'HG002',
    'NA24631': 'HG005',
}

# Reference VCF bundled with the Lambda that defines monitoring sites
MONITORING_SITES_VCF = Path(__file__).parent.parent / 'references' / 'varmon_10_sites.vcf'


# ---------------------------------------------------------------------------
# Monitoring site discovery
# ---------------------------------------------------------------------------


def load_monitoring_sites(regions_fp: Path) -> List[Tuple[str, int, str, str]]:
    """Load monitoring sites from a regions VCF file.

    Iterates records sequentially (no tabix required). Each record contributes
    one (chrom, pos_1based, ref, alt) tuple.

    Raises:
        ValueError: when no sites are found in the VCF.
    """
    sites: List[Tuple[str, int, str, str]] = []
    with pysam.VariantFile(str(regions_fp)) as vcf_fh:
        for record in vcf_fh:
            alt = record.alts[0] if record.alts else '.'
            sites.append((record.chrom, record.pos, record.ref, alt))
    if not sites:
        raise ValueError(f'No monitoring sites found in {regions_fp}')
    return sites


# ---------------------------------------------------------------------------
# VCF discovery
# ---------------------------------------------------------------------------


def _parse_s3_uri(s3_uri: str) -> Tuple[str, str]:
    """Return (bucket, key_prefix) from an s3:// URI."""
    parsed = urlparse(s3_uri)
    return parsed.netloc, parsed.path.lstrip('/')


def find_hard_filtered_vcf(output_uri: str) -> Tuple[str, str, str]:
    """
    Locate the DRAGEN hard-filtered VCF and its tabix index under output_uri.

    output_uri comes from the workflow payload engineParameters.outputUri,
    e.g. s3://pipeline-dev-cache-.../byob-icav2/.../dragen-wgts-dna/{portalRunId}/

    Returns:
        (bucket, vcf_key), (bucket, tbi_key) as a flat 4-tuple repackaged
        as (vcf_s3_uri, tbi_s3_uri).

    Raises:
        FileNotFoundError: when the VCF or index cannot be located.
    """
    bucket, prefix = _parse_s3_uri(output_uri)
    logger.info(f'Searching for hard-filtered VCF under s3://{bucket}/{prefix}')

    paginator = s3_client.get_paginator('list_objects_v2')
    vcf_key: Optional[str] = None

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if key.endswith('.hard-filtered.vcf.gz') and not key.endswith('.tbi'):
                vcf_key = key
                break
        if vcf_key:
            break

    if not vcf_key:
        raise FileNotFoundError(
            f'No hard-filtered VCF found under s3://{bucket}/{prefix}'
        )

    tbi_key = vcf_key + '.tbi'

    try:
        s3_client.head_object(Bucket=bucket, Key=tbi_key)
    except Exception:
        raise FileNotFoundError(f'Tabix index not found: s3://{bucket}/{tbi_key}')

    logger.info(f'Found VCF: s3://{bucket}/{vcf_key}')
    return bucket, vcf_key, tbi_key


# ---------------------------------------------------------------------------
# VCF download
# ---------------------------------------------------------------------------


def download_vcf(bucket: str, vcf_key: str, tbi_key: str, tmp_dir: str) -> Path:
    """Download VCF and tabix index to a local directory; return VCF path."""
    vcf_path = Path(tmp_dir) / 'input.hard-filtered.vcf.gz'
    tbi_path = Path(tmp_dir) / 'input.hard-filtered.vcf.gz.tbi'

    logger.info(f'Downloading s3://{bucket}/{vcf_key}')
    s3_client.download_file(bucket, vcf_key, str(vcf_path))

    logger.info(f'Downloading s3://{bucket}/{tbi_key}')
    s3_client.download_file(bucket, tbi_key, str(tbi_path))

    return vcf_path


# ---------------------------------------------------------------------------
# AF extraction
# ---------------------------------------------------------------------------


def extract_af_at_site(
    vcf_fh: pysam.VariantFile,
    chrom: str,
    pos: int,
    ref: str,
    alt: str,
) -> MonitoringSiteResult:
    """
    Query one monitoring site in an open pysam VariantFile.

    Uses 0-based half-open coordinates for fetch (pysam convention).
    """
    try:
        for record in vcf_fh.fetch(chrom, pos - 1, pos):
            if record.pos != pos:
                continue
            if record.ref != ref:
                continue
            if alt not in (record.alts or []):
                continue

            sample_name = list(record.samples.keys())[0]
            sample_data = record.samples[sample_name]

            dp_raw = sample_data.get('DP', 0)
            af_raw = sample_data.get('AF', [0.0])
            af_value = af_raw[0] if isinstance(af_raw, (list, tuple)) else af_raw

            filter_keys = list(record.filter.keys())
            filter_str = ';'.join(filter_keys) if filter_keys else 'PASS'

            return MonitoringSiteResult(
                chrom=chrom,
                pos=pos,
                ref=ref,
                alt=alt,
                dp=int(dp_raw) if dp_raw is not None else 0,
                af=float(af_value) if af_value is not None else 0.0,
                filter_status=filter_str,
                variant_emitted=True,
            )
    except Exception as exc:
        logger.warning(f'Error querying {chrom}:{pos} – {exc}')

    return MonitoringSiteResult(
        chrom=chrom,
        pos=pos,
        ref=ref,
        alt=alt,
        dp=0,
        af=0.0,
        filter_status='.',
        variant_emitted=False,
    )


def extract_all_sites(
    vcf_path: Path, regions_fp: Optional[Path] = None
) -> List[MonitoringSiteResult]:
    """Open VCF and extract AF at all monitoring sites.

    Args:
        vcf_path:   Path to the bgzipped + tabix-indexed DRAGEN hard-filtered VCF.
        regions_fp: Path to the monitoring sites VCF. Defaults to the bundled
                    varmon_10_sites.vcf alongside this package.
    """
    if regions_fp is None:
        regions_fp = MONITORING_SITES_VCF
    monitoring_sites = load_monitoring_sites(regions_fp)
    results: List[MonitoringSiteResult] = []
    with pysam.VariantFile(str(vcf_path)) as vcf_fh:
        for chrom, pos, ref, alt in monitoring_sites:
            result = extract_af_at_site(vcf_fh, chrom, pos, ref, alt)
            results.append(result)
            logger.info(
                f'{chrom}:{pos} {ref}>{alt} – DP={result.dp} AF={result.af:.4f} '
                f'emitted={result.variant_emitted}'
            )
    return results


# ---------------------------------------------------------------------------
# EventBridge emission
# ---------------------------------------------------------------------------


def emit_result(detail: VariantMonitoringResultDetail) -> str:
    """Put a VariantMonitoringResult event onto the OrcaBus event bus."""
    response = events_client.put_events(
        Entries=[
            {
                'Source': EVENT_SOURCE,
                'DetailType': EVENT_DETAIL_TYPE,
                'Detail': detail.model_dump_json(),
                'EventBusName': EVENT_BUS_NAME,
            }
        ]
    )

    if response['FailedEntryCount'] > 0:
        errors = [
            e.get('ErrorMessage', 'unknown') for e in response['Entries'] if 'ErrorCode' in e
        ]
        raise RuntimeError(f'EventBridge put_events failed: {errors}')

    return response['Entries'][0]['EventId']


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda entrypoint.

    Parses the WorkflowRunStateChange event, extracts allele frequencies from
    the DRAGEN hard-filtered VCF at the outputUri in the event payload, and
    emits a VariantMonitoringResult event to the OrcaBus EventBridge bus.
    """
    logger.info(f'Received event: {json.dumps(event, default=str)}')

    incoming = IncomingEvent.model_validate(event)
    detail = cast(WorkflowRunStateChangeDetail, incoming.detail)

    if detail.status != SUCCEEDED_STATUS:
        logger.info(f'Skipping non-SUCCEEDED event (status={detail.status})')
        return {'statusCode': 200, 'skipped': True, 'reason': f'status={detail.status}'}

    portal_run_id = detail.portalRunId
    analysis_name = detail.workflowRunName

    # ---- Extract fields from the nested payload.data block ----
    payload_data = detail.payload.data if (detail.payload and detail.payload.data) else None
    tags = payload_data.tags if payload_data else None
    engine_params = payload_data.engineParameters if payload_data else None

    library_id = tags.libraryId if tags else None
    subject_id = tags.subjectId if tags else None
    individual_id = tags.individualId if tags else None
    giab_id = INDIVIDUAL_ID_TO_GIAB_ID.get(individual_id) if individual_id else None

    logger.info(
        f'Processing SUCCEEDED analysis: name={analysis_name} '
        f'portalRunId={portal_run_id} libraryId={library_id}'
    )

    output_uri = engine_params.outputUri if engine_params else None
    if not output_uri:
        raise ValueError(
            f'payload.data.engineParameters.outputUri missing from event for portalRunId={portal_run_id}'
        )

    # ---- Narrow VCF search to germline subdirectory when available ----
    outputs = payload_data.outputs if payload_data else None
    germline_rel_path = outputs.dragenGermlineVariantCallingOutputRelPath if outputs else None
    vcf_search_uri = output_uri
    if germline_rel_path:
        vcf_search_uri = output_uri.rstrip('/') + '/' + germline_rel_path
        logger.info(f'Narrowing VCF search to germline path: {vcf_search_uri}')
    else:
        logger.info(f'No germline output path in payload; searching full outputUri: {output_uri}')

    logger.info(
        f'subjectId={subject_id} individualId={individual_id} outputUri={output_uri}'
    )

    # ---- VCF extraction ----
    bucket, vcf_key, tbi_key = find_hard_filtered_vcf(vcf_search_uri)

    with tempfile.TemporaryDirectory(dir='/tmp') as tmp_dir:
        vcf_path = download_vcf(bucket, vcf_key, tbi_key, tmp_dir)
        site_results = extract_all_sites(vcf_path)

    n_emitted = sum(1 for r in site_results if r.variant_emitted)
    logger.info(f'Extracted AF at {n_emitted}/{len(site_results)} sites')

    result_detail = VariantMonitoringResultDetail(
        portalRunId=portal_run_id,
        libraryId=library_id,
        subjectId=subject_id,
        individualId=individual_id,
        giabId=giab_id,
        analysisName=analysis_name,
        outputUri=output_uri,
        extractedAt=datetime.now(tz=timezone.utc),
        monitoringSites=site_results,
    )

    event_id = emit_result(result_detail)
    logger.info(f'Emitted VariantMonitoringResult with id: {event_id}')

    return {'statusCode': 200, 'eventId': event_id}
