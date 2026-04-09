"""
Pydantic models for variant monitoring Lambda.

Incoming: WorkflowRunStateChange from orcabus.workflowmanager
Outgoing: VariantMonitoringResult emitted by this service
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ---- Incoming event models (WorkflowRunStateChange) ----


class WrscWorkflow(BaseModel):
    """Workflow definition sub-object."""

    orcabusId: str
    name: str
    version: str
    codeVersion: Optional[str] = None
    executionEngine: Optional[str] = None

    model_config = {'extra': 'allow'}


class WrscLibrary(BaseModel):
    """Library entry within a WorkflowRun."""

    orcabusId: str
    libraryId: str

    model_config = {'extra': 'allow'}


class WrscTags(BaseModel):
    """Metadata tags embedded in the payload data."""

    libraryId: Optional[str] = None
    subjectId: Optional[str] = None
    individualId: Optional[str] = None

    model_config = {'extra': 'allow'}


class WrscEngineParameters(BaseModel):
    """Engine parameters embedded in the payload data."""

    outputUri: Optional[str] = None
    logsUri: Optional[str] = None
    projectId: Optional[str] = None
    pipelineId: Optional[str] = None

    model_config = {'extra': 'allow'}


class WrscOutputs(BaseModel):
    """Workflow output paths embedded in the payload data."""

    dragenGermlineVariantCallingOutputRelPath: Optional[str] = None

    model_config = {'extra': 'allow'}


class WrscPayloadData(BaseModel):
    """The 'data' block inside a WorkflowRunStateChange payload."""

    tags: Optional[WrscTags] = None
    engineParameters: Optional[WrscEngineParameters] = None
    outputs: Optional[WrscOutputs] = None

    model_config = {'extra': 'allow'}


class WrscPayload(BaseModel):
    """Payload wrapper for a WorkflowRunStateChange event."""

    orcabusId: Optional[str] = None
    refId: Optional[str] = None
    version: Optional[str] = None
    data: Optional[WrscPayloadData] = None

    model_config = {'extra': 'allow'}


class WorkflowRunStateChangeDetail(BaseModel):
    """detail payload of a WorkflowRunStateChange EventBridge event."""

    id: Optional[str] = None
    orcabusId: Optional[str] = None
    portalRunId: str
    workflowRunName: str
    workflow: WrscWorkflow
    libraries: List[WrscLibrary] = []
    status: str
    timestamp: Optional[datetime] = None
    payload: Optional[WrscPayload] = None

    model_config = {'extra': 'allow'}


class IncomingEvent(BaseModel):
    """EventBridge event envelope as delivered to Lambda."""

    id: Optional[str] = None
    source: str
    time: Optional[datetime] = None
    account: Optional[str] = None
    region: Optional[str] = None
    detail_type: str = Field(..., alias='detail-type')
    detail: WorkflowRunStateChangeDetail

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
    giabId: Optional[str] = None
    analysisName: str
    outputUri: Optional[str] = None
    extractedAt: datetime
    monitoringSites: List[MonitoringSiteResult]
