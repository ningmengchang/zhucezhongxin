from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .codex_runner import CodexRunner
from .config import PROJECT_DIR, settings
from .database import Database
from .ingestion import ingest_document
from .models import (
    DocumentCreate,
    DocumentOut,
    JobOut,
    JobStatus,
    QuestionAccepted,
    QuestionRequest,
    SearchRequest,
)
from .local_library import sync_local_library
from .qa_service import QAService
from .retrieval import Retriever
from .seed import seed


settings.ensure_directories()
database = Database(settings.database_path)
database.init()
retriever = Retriever(database)
codex_runner = CodexRunner(settings)
qa_service = QAService(database, retriever, codex_runner, settings.queue_limit)


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.init()
    if settings.auto_seed and not database.list_documents():
        await asyncio.to_thread(seed, database)
    if settings.auto_sync_local_files:
        await asyncio.to_thread(sync_local_library, database)
    await qa_service.start(settings.codex_concurrency)
    yield
    await qa_service.stop()


app = FastAPI(
    title="法规研究与证据问答助手",
    version="0.7.0",
    description="等权检索全部本地法规文件，必要时查询欧盟官方Web来源，并通过本机 Codex CLI 生成正文优先的详实法规答复。",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


Role = Literal["admin", "user"]


def current_role(x_role: Annotated[str | None, Header()] = None) -> Role:
    value = (x_role or "user").lower()
    if value not in {"admin", "user"}:
        raise HTTPException(status_code=403, detail="无效角色")
    return value  # type: ignore[return-value]


def require_admin(role: Annotated[Role, Depends(current_role)]) -> Role:
    if role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return role


@app.get("/api/health")
async def health() -> dict:
    index = database.search_index_status()
    return {
        "status": "ok" if index["ready"] else "degraded",
        "documents": index["documents"],
        "queue": qa_service.queue.qsize(),
        "searchIndex": index,
    }


@app.get("/api/system/codex-health")
async def codex_health() -> dict:
    result = await codex_runner.health()
    result["queueSize"] = qa_service.queue.qsize()
    result["queueLimit"] = settings.queue_limit
    result["verifyEnabled"] = settings.codex_verify_enabled
    result["webSearchEnabled"] = settings.web_search_enabled
    result["localResearchRounds"] = settings.research_local_rounds
    result["webResearchRounds"] = settings.research_web_rounds
    result["researchMode"] = "mcp-on-demand" if settings.law_retrieval_mode == "mcp" else "legacy-adaptive-fast"
    result["retrievalMode"] = settings.law_retrieval_mode
    result["searchIndex"] = database.search_index_status()
    result["webTrigger"] = "explicit-request-or-critical-legal-gap"
    result["localSourcePolicy"] = "all-files-equal-no-review-state"
    result["reportMode"] = "prose-first-v4"
    result["reportExpansionEnabled"] = settings.report_expansion_enabled
    return result


@app.post("/api/questions", response_model=QuestionAccepted, status_code=202)
async def ask(request: QuestionRequest) -> QuestionAccepted:
    try:
        job_id = await qa_service.submit(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return QuestionAccepted(job_id=job_id, status=JobStatus.queued)


@app.get("/api/questions/{job_id}", response_model=JobOut)
async def question_result(job_id: str) -> JobOut:
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return JobOut.model_validate(job)


@app.get("/api/questions/{job_id}/events")
async def question_events(job_id: str) -> StreamingResponse:
    if not database.get_job(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")

    async def stream() -> AsyncIterator[str]:
        delivered = 0
        heartbeat = 0
        while True:
            job = database.get_job(job_id)
            if not job:
                break
            events = job["events"]
            for event in events[delivered:]:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            delivered = len(events)
            if job["status"] in {"completed", "failed"}:
                yield f"event: done\ndata: {json.dumps({'status': job['status']})}\n\n"
                break
            heartbeat += 1
            if heartbeat % 30 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/search")
async def search(request: SearchRequest) -> list[dict]:
    return [
        item.model_dump(mode="json")
        for item in retriever.search(
            request.query,
            as_of_date=request.as_of_date,
            include_future_rules=request.include_future_rules,
            limit=request.limit,
        )
    ]


@app.get("/api/evidence/{evidence_id}")
async def evidence(evidence_id: str) -> dict:
    item = database.get_passage(evidence_id)
    if not item:
        raise HTTPException(status_code=404, detail="证据不存在")
    item["pdfUrl"] = f"/api/documents/{item['document_id']}/file#page={item['page']}"
    return item


@app.get("/api/documents", response_model=list[DocumentOut])
async def documents() -> list[DocumentOut]:
    return [DocumentOut.model_validate(item) for item in database.list_documents()]


@app.get("/api/documents/{document_id}/file")
async def document_file(document_id: str) -> FileResponse:
    document = database.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="法规文件不存在")
    path = Path(document["path"]).resolve()
    allowed_roots = [settings.source_dir.resolve(), settings.data_dir.resolve()]
    if not any(path.is_relative_to(root) for root in allowed_roots):
        raise HTTPException(status_code=403, detail="文件不在允许目录")
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else "text/plain"
    return FileResponse(
        path,
        filename=document["filename"],
        media_type=media_type,
        content_disposition_type="inline",
    )


@app.post("/api/documents/ingest-existing", response_model=DocumentOut)
async def ingest_existing(
    spec: DocumentCreate,
    _: Annotated[Role, Depends(require_admin)],
) -> DocumentOut:
    try:
        document = await asyncio.to_thread(ingest_document, database, spec, settings.source_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DocumentOut.model_validate(document)


@app.get("/api/documents/{document_id}/versions")
async def document_versions(document_id: str) -> list[dict]:
    versions = database.version_chain(document_id)
    if not versions:
        raise HTTPException(status_code=404, detail="法规文件不存在")
    return versions


@app.post("/api/admin/seed")
async def seed_documents(_: Annotated[Role, Depends(require_admin)]) -> dict:
    results = await asyncio.to_thread(seed, database)
    return {"count": len(results), "results": results}


@app.post("/api/admin/sync-local")
async def sync_local_documents(_: Annotated[Role, Depends(require_admin)]) -> dict:
    return await asyncio.to_thread(sync_local_library, database)


frontend_dist = PROJECT_DIR / "frontend" / "dist"
if frontend_dist.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
