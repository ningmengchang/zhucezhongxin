from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    queued = "queued"
    retrieving = "retrieving"
    assessing = "assessing"
    web_searching = "web_searching"
    generating = "generating"
    verifying = "verifying"
    completed = "completed"
    failed = "failed"


class DocumentCreate(BaseModel):
    path: str
    title: str | None = None
    jurisdiction: str = "EU"
    authority: str | None = None
    official_url: str | None = None
    publication_date: date | None = None
    effective_date: date | None = None
    expiry_date: date | None = None
    version_of: str | None = None


class DocumentOut(BaseModel):
    id: str
    title: str
    filename: str
    path: str
    sha256: str
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


class ReportParagraph(BaseModel):
    paragraphId: str
    text: str
    nature: Literal["legal_analysis", "process_explanation", "implementation_advice", "uncertainty"]
    sourceRefs: list[str] = Field(default_factory=list)


class ReportSection(BaseModel):
    title: str
    paragraphs: list[ReportParagraph] = Field(default_factory=list)


class ServiceProviderItem(BaseModel):
    itemId: str
    title: str
    detail: str
    nature: Literal[
        "direct_legal_duty",
        "direct_service_provider_duty",
        "legal_responsibility_boundary",
        "derived_implementation_action",
    ]
    sourceRefs: list[str] = Field(default_factory=list)


class ReportSource(BaseModel):
    sourceId: str
    origin: Literal["local", "web"]
    authorityKind: Literal["legal_act", "official_guidance", "local_supplement"]
    title: str
    documentId: str | None = None
    filename: str | None = None
    article: str | None = None
    page: int | None = None
    quote: str
    url: str | None = None
    publisher: str | None = None
    publicationDate: date | None = None
    effectiveDate: date | None = None
    applicabilityStatus: Literal["current", "future", "unknown"] = "unknown"
    accessedAt: datetime | None = None
    verified: bool = True


class ResearchTraceItem(BaseModel):
    round: int
    type: Literal["local", "web", "verification"]
    queries: list[str] = Field(default_factory=list)
    resultCount: int = 0
    decision: str
    action: Literal["search", "read"] | None = None
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    durationMs: float | None = Field(default=None, ge=0)
    modelThinkingMs: float | None = Field(default=None, ge=0)
    finalAnswerMs: float | None = Field(default=None, ge=0)


class ResearchReport(BaseModel):
    schemaVersion: Literal["3.0"] = "3.0"
    status: Literal["answered", "partial", "unresolved", "system_error"]
    executiveSummary: list[ReportParagraph] = Field(default_factory=list)
    timeContext: TimeContext | None = None
    analysisSections: list[ReportSection] = Field(default_factory=list)
    serviceProviderImpact: list[ServiceProviderItem] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    sources: list[ReportSource] = Field(default_factory=list)
    researchTrace: list[ResearchTraceItem] = Field(default_factory=list)


ContentBasis = Literal[
    "regulatory_requirement",
    "official_interpretation",
    "standard_guidance",
    "engineering_recommendation",
    "uncertainty",
]


class ReportStatementV4(BaseModel):
    statementId: str
    text: str
    basis: ContentBasis
    sourceRefs: list[str] = Field(default_factory=list)


class WorkProductItem(BaseModel):
    itemId: str
    title: str = ""
    detail: str = ""
    cells: list[str] = Field(default_factory=list)
    owner: str = ""
    input: str = ""
    output: str = ""
    acceptanceCriteria: str = ""
    priority: Literal["high", "medium", "low", "not_applicable"] = "not_applicable"
    basis: ContentBasis
    sourceRefs: list[str] = Field(default_factory=list)


class WorkProductBlock(BaseModel):
    blockId: str
    blockType: Literal[
        "deep_analysis",
        "comparison_table",
        "field_matrix",
        "responsibility_matrix",
        "process",
        "checklist",
        "decision_rules",
        "example",
        "common_mistakes",
    ]
    title: str
    introduction: str = ""
    columns: list[str] = Field(default_factory=list)
    items: list[WorkProductItem] = Field(default_factory=list)


class ServiceProviderActionV4(BaseModel):
    actionId: str
    title: str
    detail: str
    owner: str
    priority: Literal["high", "medium", "low"]
    deliverable: str
    nature: Literal[
        "direct_service_provider_duty",
        "legal_responsibility_boundary",
        "derived_implementation_action",
    ]
    basis: ContentBasis
    sourceRefs: list[str] = Field(default_factory=list)


class UnresolvedQuestionV4(BaseModel):
    itemId: str
    question: str
    impact: str
    neededEvidence: str


class QualityNoteV4(BaseModel):
    noteId: str
    type: Literal["removed", "partial_support", "coverage", "system"]
    message: str
    relatedId: str | None = None


class WorkProductReport(BaseModel):
    schemaVersion: Literal["4.0"] = "4.0"
    status: Literal["answered", "partial", "unresolved", "system_error"]
    answerTitle: str
    executiveAnswer: list[ReportStatementV4] = Field(default_factory=list)
    keyTakeaways: list[ReportStatementV4] = Field(default_factory=list)
    contentBlocks: list[WorkProductBlock] = Field(default_factory=list)
    serviceProviderActions: list[ServiceProviderActionV4] = Field(default_factory=list)
    unresolvedQuestions: list[UnresolvedQuestionV4] = Field(default_factory=list)
    timeContext: TimeContext | None = None
    sources: list[ReportSource] = Field(default_factory=list)
    researchTrace: list[ResearchTraceItem] = Field(default_factory=list)
    qualityNotes: list[QualityNoteV4] = Field(default_factory=list)


AnswerPayload = Annotated[LegalAnswer | ResearchReport | WorkProductReport, Field(discriminator="schemaVersion")]


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
    answer: AnswerPayload | None = None
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
    jurisdiction: str = "EU"
    authority: str | None = None
