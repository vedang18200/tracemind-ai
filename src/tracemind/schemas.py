from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(StrictModel):
    id: str
    url: str
    title: str
    retrieved_at: datetime
    content_hash: str


class Finding(StrictModel):
    id: str
    claim: str
    source_id: str
    excerpt: str
    confidence: float = Field(ge=0, le=1)  
    verification: Literal["unverified", "verified", "needs_review", "unsupported"] = "unverified"


class PlanStep(StrictModel):
    id: str
    description: str
    agent: Literal["researcher", "analyst", "verifier", "reporter"]
    status: Literal["pending", "done", "failed"] = "pending"


class VerificationResult(StrictModel):
    finding_id: str
    status: Literal["verified", "needs_review", "unsupported"]
    reason: str

#supervisor agent schema 
class SupervisorPlan(StrictModel):
    task_type: Literal["competitor_analysis", "unsupported"]
    steps: list[PlanStep] = Field(max_length=12)

#researcher agent schema
class ExtractedFinding(StrictModel):
    claim: str
    excerpt: str
    confidence: float = Field(ge=0, le=1)

class PageExtraction(StrictModel):
    findings: list[ExtractedFinding]


class SupervisorPlan(StrictModel):
    task_type: Literal["competitor_analysis", "unsupported"]
    steps: list[PlanStep] = Field(max_length=12)
    search_queries: list[str] = Field(default_factory=list, max_length=5)

class PlatformSummary(StrictModel):
    name: str
    pricing: str | None = None         
    features: list[str]
    finding_ids: list[str]

class Conclusion(StrictModel):
    statement: str
    finding_ids: list[str]

#analyst agent schema
class AnalysisResult(StrictModel):
    platforms: list[PlatformSummary]
    market_gaps: list[Conclusion]      
    missing_info: list[str]

#verifier agent schema
class VerificationOutput(StrictModel):
    results: list[VerificationResult]

#reporter agent schema
class ReportSection(StrictModel):
    heading: str
    body: str


class ReportDraft(StrictModel):
    sections: list[ReportSection]