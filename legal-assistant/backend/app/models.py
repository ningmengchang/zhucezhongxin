from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class SourceLevel(str, Enum):
    A1 = "A1"
    A2 = "A2"
    B = "B"
    C = "C"
    D = "D"


class ReviewStatus(str, Enum):
    pending = "pending"
    published = "published"
    rejected = "rejected"
    disabled = "disabled"


class JobStatus(str, Enum):
    queued = "queued"
    retrieving = "retrieving"
    generating = "generating"
    verifying = "verifying"
    completed = "completed"
    failed = "failed"


class DocumentCreate(BaseModel):
    path: str
    title: str | None = None
    source_level: SourceLevel = SourceLevel.D
    review_status: ReviewStatus = ReviewStatus.pending
    jurisdiction: str = "EU"
    authority: str | None = None
    official_url: str | None = None
    publication_date: date | None = None
    effective_date: date | None = None
    expiry_date: date | None = None
    version_of: str | None = None


class DocumentReview(BaseModel):
    review_status: ReviewStatus
    source_level: SourceLevel | None = None
    note: str | None = None


class DocumentOut(BaseModel):
    id: str
    title: str
    filename: str
    path: str
    sha256: str
    source_level: SourceLevel
    review_status: ReviewStatus
    jurisdiction: str
    authority: str | None = None
    official_url: str | None = None
    publication_date: str | None = None
    effective_date: str | None = None
    expiry_date: str | None = None
    version_of: str | None = None
    passage_count: int = 0
    created_at: str


class Evidence(BaseModel):
    evidenceId: str
    documentId: str
    documentTitle: str
    filename: str
    sourceLevel: SourceLevel
    article: str | None = None
    paragraph: str | None = None
    page: int
    quote: str
    officialUrl: str | None = None
    publicationDate: date | None = None
    effectiveDate: date | None = None
    expiryDate: date | None = None
    applicabilityStatus: Literal["current", "future", "unknown"] = "unknown"
    score: float = 0.0


class Citation(BaseModel):
    evidenceId: str
    documentId: str
    sourceLevel: SourceLevel
    article: str | None = None
    page: int
    quote: str
    interpretation: str
    documentTitle: str | None = None
    filename: str | None = None
    publicationDate: date | None = None
    effectiveDate: date | None = None
    expiryDate: date | None = None
    applicabilityStatus: Literal["current", "future", "unknown"] = "unknown"


class Claim(BaseModel):
    claimId: str
    text: str
    analysis: str = ""
    nature: Literal["legal_fact", "implementation_advice", "uncertain"]
    supportStatus: Literal["supported", "partially_supported", "unsupported"]
    citations: list[Citation] = Field(default_factory=list)


class ImplementationAdvice(BaseModel):
    advice: str
    basis: str
    nature: Literal["implementation_advice"] = "implementation_advice"


class TimeNotice(BaseModel):
    documentId: str
    documentTitle: str
    applicabilityStatus: Literal["current", "future", "unknown"]
    effectiveDate: date | None = None
    note: str


class TimeContext(BaseModel):
    asOfDate: date
    includesFutureRules: bool = True
    notices: list[TimeNotice] = Field(default_factory=list)


class LegalAnswer(BaseModel):
    schemaVersion: Literal["2.0"] = "2.0"
    status: Literal["answered", "partial", "insufficient_evidence", "system_error"]
    summary: str
    timeContext: TimeContext | None = None
    claims: list[Claim] = Field(default_factory=list)
    implementationAdvice: list[ImplementationAdvice] = Field(default_factory=list)
    missingEvidence: list[str] = Field(default_factory=list)
    searchedSources: list[str] = Field(default_factory=list)


class QuestionRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    conversation_id: str | None = Field(default=None, min_length=8, max_length=128)
    as_of_date: date | None = None
    include_future_rules: bool = True


class QuestionAccepted(BaseModel):
    job_id: str
    status: JobStatus


class JobOut(BaseModel):
    id: str
    question: str
    conversation_id: str | None = None
    status: JobStatus
    answer: LegalAnswer | None = None
    error: str | None = None
    events: list[dict] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    as_of_date: date | None = None
    include_future_rules: bool = True
    limit: int = Field(default=12, ge=1, le=50)


class OfficialArchiveRequest(BaseModel):
    url: str = Field(min_length=12, max_length=2000)
    title: str
    source_level: SourceLevel = SourceLevel.A1
    jurisdiction: str = "EU"
    authority: str | None = None
