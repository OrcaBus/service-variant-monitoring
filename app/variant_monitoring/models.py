"""
Pydantic models for variant monitoring Lambda.

Incoming: Icav2WesAnalysisStateChange from orcabus.icav2wesmanager
Outgoing: VariantMonitoringResult emitted by this service
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---- Incoming event models ----


class Icav2WesEventTags(BaseModel):
    """Tags attached to an ICAv2 WES analysis by the workflow manager."""

    portalRunId: str
    libraryId: Optional[str] = None
    subjectId: Optional[str] = None
    individualId: Optional[str] = None

    model_config = {'extra': 'allow'}


class Icav2WesAnalysisStateChangeDetail(BaseModel):
    """detail payload of an Icav2WesAnalysisStateChange EventBridge event."""

    id: Optional[str] = None
    name: str
    status: str
    tags: Icav2WesEventTags
    inputs: Optional[Dict[str, Any]] = None
    outputs: Optional[Dict[str, Any]] = None
    engineParameters: Optional[Dict[str, Any]] = None

    model_config = {'extra': 'allow'}


class IncomingEvent(BaseModel):
    """EventBridge event envelope as delivered to Lambda."""

    id: Optional[str] = None
    source: str
    time: Optional[datetime] = None
    account: Optional[str] = None
    region: Optional[str] = None
    detail_type: str = Field(..., alias='detail-type')
    detail: Icav2WesAnalysisStateChangeDetail

    model_config = {'populate_by_name': True}


# ---- Outgoing event models ----


class MonitoringSiteResult(BaseModel):
    """AF extraction result for one variant monitoring site."""

    chrom: str
    pos: int
    ref: str
    alt: str
    dp: int
    af: float
    filter_status: str
    variant_emitted: bool


class VariantMonitoringResultDetail(BaseModel):
    """Payload emitted to EventBridge after extracting AFs from a DRAGEN VCF."""

    portalRunId: str
    libraryId: Optional[str] = None
    subjectId: Optional[str] = None
    individualId: Optional[str] = None
    analysisName: str
    outputUri: Optional[str] = None
    extractedAt: datetime
    monitoringSites: List[MonitoringSiteResult]
