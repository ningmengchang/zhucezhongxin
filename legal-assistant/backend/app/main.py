from __future__ import annotations

import asyncio
import copy
import json
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Annotated, AsyncIterator
from zoneinfo import ZoneInfo

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .codex_runner import CodexRunner
from .auth import AuthService, Principal, SESSION_COOKIE
from .config import PROJECT_DIR, settings
from .database import Database
from .ingestion import ingest_document
from .models import (
    DocumentCreate,
    DocumentOut,
    AuthUserOut,
    JobOut,
    JobStatus,
    LoginOut,
    LoginRequest,
    RegisterOut,
    RegisterRequest,
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
auth_service = AuthService(database)
auth_service.seed_defaults()
retriever = Retriever(database)
codex_runner = CodexRunner(settings)
qa_service = QAService(database, retriever, codex_runner, settings.queue_limit)


async def cleanup_monitoring_loop() -> None:
    while True:
        await asyncio.sleep(24 * 60 * 60)
        await asyncio.to_thread(database.cleanup_monitoring_data, 30)


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.init()
    await asyncio.to_thread(database.cleanup_monitoring_data, 30)
    await asyncio.to_thread(
        database.fail_unfinished_jobs,
        "服务已重启，未完成的历史任务已终止，请重新提问",
    )
    auth_service.seed_defaults()
    if settings.auto_seed and not database.list_documents():
        await asyncio.to_thread(seed, database)
    if settings.auto_sync_local_files:
        await asyncio.to_thread(sync_local_library, database)
    with suppress(Exception):
        await codex_runner.start()
    await qa_service.start(settings.codex_concurrency)
    cleanup_task = asyncio.create_task(cleanup_monitoring_loop())
    yield
    cleanup_task.cancel()
    with suppress(asyncio.CancelledError):
        await cleanup_task
    await qa_service.stop()
    await codex_runner.stop()


app = FastAPI(
    title="法规助手",
    version="0.8.0",
    description="通过本机 Codex App Server提供等权本地文件检索、原文读取和Web搜索，由模型自主研究并生成原生Markdown答复。",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


AUTH_MESSAGES = {
    "authentication_required": "请先登录",
    "session_invalid": "登录已失效，请重新登录",
    "session_replaced": "该账号已在其他客户端登录，当前会话已失效",
    "session_expired": "登录已超过24小时，请重新登录",
    "account_disabled": "账号已停用",
    "admin_revoked": "管理员已将当前会话下线",
}


def current_user(
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> Principal:
    principal, code = auth_service.authenticate(session_token)
    if not principal:
        raise HTTPException(
            status_code=401,
            detail={"code": code, "message": AUTH_MESSAGES.get(code, "请重新登录")},
        )
    return principal


def require_admin(principal: Annotated[Principal, Depends(current_user)]) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return principal


def auth_user_out(principal: Principal) -> AuthUserOut:
    return AuthUserOut(
        username=principal.username,
        role=principal.role,
        permissions=principal.permissions,
        session_id=principal.session_id if principal.role == "admin" else None,
    )


@app.post("/api/auth/login", response_model=LoginOut)
async def login(payload: LoginRequest, request: Request, response: Response) -> LoginOut:
    replaced_session_ids = await asyncio.to_thread(
        database.active_session_ids_for_username, payload.username
    )
    result = await asyncio.to_thread(
        auth_service.login,
        payload.username,
        payload.password,
        login_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    if not result:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    principal, token, expires_at = result
    await asyncio.gather(
        *(qa_service.close_session(session_id) for session_id in replaced_session_ids),
        return_exceptions=True,
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=24 * 60 * 60,
        httponly=True,
        secure=False,
        samesite="strict",
        path="/",
    )
    return LoginOut(user=auth_user_out(principal), expires_at=expires_at)


@app.post("/api/auth/register", response_model=RegisterOut, status_code=201)
async def register(payload: RegisterRequest) -> RegisterOut:
    try:
        username = await asyncio.to_thread(
            auth_service.register,
            payload.username,
            payload.password,
            payload.confirm_password,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RegisterOut(username=username, message="注册成功，请登录")


@app.get("/api/auth/me", response_model=AuthUserOut)
async def me(principal: Annotated[Principal, Depends(current_user)]) -> AuthUserOut:
    return auth_user_out(principal)


@app.post("/api/auth/logout", status_code=204)
async def logout(
    response: Response,
    principal: Annotated[Principal, Depends(current_user)],
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> Response:
    await asyncio.to_thread(auth_service.logout, session_token)
    await qa_service.close_session(principal.session_id)
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")
    response.status_code = 204
    return response


@app.get("/api/health")
async def health(_: Annotated[Principal, Depends(require_admin)]) -> dict:
    index = database.search_index_status()
    return {
        "status": "ok" if index["ready"] else "degraded",
        "documents": index["documents"],
        "queue": qa_service.queue.qsize(),
        "searchIndex": index,
    }


@app.get("/api/system/codex-health")
async def codex_health(_: Annotated[Principal, Depends(require_admin)]) -> dict:
    result = await codex_runner.health()
    result["queueSize"] = qa_service.queue.qsize()
    result["queueLimit"] = settings.queue_limit
    result["independentReview"] = False
    result["webSearchEnabled"] = True
    result["researchMode"] = "single-agent-autonomous"
    result["retrievalMode"] = "mcp-and-native-web"
    result["searchIndex"] = database.search_index_status()
    result["webTrigger"] = "model-decides"
    result["localSourcePolicy"] = "all-files-equal-no-review-state"
    result["conversationMemoryEnabled"] = True
    result["conversationMemoryScope"] = "login-session"
    result["conversationMemoryMaxTurns"] = settings.codex_conversation_max_turns
    result["conversationMemoryIdleSeconds"] = settings.codex_conversation_idle_seconds
    result["conversationMemoryPersistsAcrossRestart"] = False
    result["reportMode"] = "native-markdown-v7"
    return result


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _date_range(date_from: str | None, date_to: str | None) -> tuple[str | None, str | None]:
    def boundary(value: str, *, next_day: bool = False) -> str:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="日期格式必须是 YYYY-MM-DD") from exc
        if next_day:
            parsed += timedelta(days=1)
        local = datetime.combine(parsed, time.min, tzinfo=SHANGHAI)
        return local.astimezone(timezone.utc).isoformat()

    return (
        boundary(date_from) if date_from else None,
        boundary(date_to, next_day=True) if date_to else None,
    )


def _online_cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()


@app.get("/api/admin/monitor/overview")
async def monitor_overview(_: Annotated[Principal, Depends(require_admin)]) -> dict:
    local_today = datetime.now(SHANGHAI).date()
    day_start, day_end = _date_range(local_today.isoformat(), local_today.isoformat())
    return database.monitoring_overview(
        day_start=day_start, day_end=day_end, online_cutoff=_online_cutoff()
    )


@app.get("/api/admin/monitor/users")
async def monitor_users(_: Annotated[Principal, Depends(require_admin)]) -> list[dict]:
    return database.monitoring_users(online_cutoff=_online_cutoff())


@app.get("/api/admin/monitor/sessions")
async def monitor_sessions(
    _: Annotated[Principal, Depends(require_admin)],
    page: int = 1,
    page_size: int = 20,
    username: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    start, end = _date_range(date_from, date_to)
    return database.monitoring_sessions(
        page=page, page_size=page_size, username=username, status=status,
        date_from=start, date_to=end, online_cutoff=_online_cutoff(),
    )


@app.post("/api/admin/monitor/sessions/{session_id}/revoke", status_code=204)
async def monitor_revoke_session(
    session_id: str,
    principal: Annotated[Principal, Depends(require_admin)],
) -> Response:
    if session_id == principal.session_id:
        raise HTTPException(status_code=400, detail="不能下线当前使用中的管理员会话")
    if not database.revoke_session_by_id(session_id):
        raise HTTPException(status_code=404, detail="会话不存在或已经失效")
    await qa_service.close_session(session_id)
    return Response(status_code=204)


@app.get("/api/admin/monitor/questions")
async def monitor_questions(
    _: Annotated[Principal, Depends(require_admin)],
    page: int = 1,
    page_size: int = 20,
    username: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    start, end = _date_range(date_from, date_to)
    return database.monitoring_questions(
        page=page, page_size=page_size, username=username, status=status, keyword=keyword,
        date_from=start, date_to=end,
    )


@app.get("/api/admin/monitor/questions/{job_id}")
async def monitor_question_detail(
    job_id: str,
    _: Annotated[Principal, Depends(require_admin)],
) -> dict:
    job = database.monitoring_question_detail(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="问题记录不存在")
    return job


@app.post("/api/questions", response_model=QuestionAccepted, status_code=202)
async def ask(
    request: QuestionRequest,
    principal: Annotated[Principal, Depends(current_user)],
) -> QuestionAccepted:
    try:
        job_id = await qa_service.submit(request, principal.user_id, principal.session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return QuestionAccepted(job_id=job_id, status=JobStatus.queued)


INTERNAL_ANSWER_FIELDS = {"researchTrace", "qualityNotes"}


def _remove_internal_answer_fields(value):
    if isinstance(value, dict):
        return {
            key: _remove_internal_answer_fields(item)
            for key, item in value.items()
            if key not in INTERNAL_ANSWER_FIELDS
        }
    if isinstance(value, list):
        return [_remove_internal_answer_fields(item) for item in value]
    return value


def job_for_principal(job: dict, principal: Principal) -> dict:
    public_fields = {
        "id", "question", "conversation_id", "status", "answer",
        "error", "events", "created_at", "updated_at",
    }
    visible = {key: copy.deepcopy(value) for key, value in job.items() if key in public_fields}
    if principal.role == "user":
        if visible.get("status") not in {"queued", "completed", "failed"}:
            visible["status"] = "processing"
        if visible.get("answer") is not None:
            visible["answer"] = _remove_internal_answer_fields(visible["answer"])
        if visible.get("error"):
            visible["error"] = "问题处理失败，请稍后重试"
        visible["events"] = [
            {
                "stage": "completed" if item.get("stage") == "completed" else "processing",
                "message": "答复已完成" if item.get("stage") == "completed" else "正在处理，请稍候",
                "at": item.get("at"),
            }
            for item in visible.get("events", [])
        ]
    return visible


@app.get("/api/questions/{job_id}", response_model=None)
async def question_result(
    job_id: str,
    principal: Annotated[Principal, Depends(current_user)],
) -> JobOut:
    job = database.get_job(job_id)
    if not job or job.get("created_by") != principal.user_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job_for_principal(job, principal)


@app.get("/api/questions/{job_id}/events")
async def question_events(
    job_id: str,
    principal: Annotated[Principal, Depends(current_user)],
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> StreamingResponse:
    job = database.get_job(job_id)
    if not job or job.get("created_by") != principal.user_id:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def stream() -> AsyncIterator[str]:
        delivered = 0
        heartbeat = 0
        while True:
            active_principal, code = auth_service.authenticate(session_token)
            if not active_principal or active_principal.user_id != principal.user_id:
                message = AUTH_MESSAGES.get(code, "登录已失效，请重新登录")
                yield f"event: auth\ndata: {json.dumps({'code': code, 'message': message}, ensure_ascii=False)}\n\n"
                break
            job = database.get_job(job_id)
            if not job:
                break
            events = job["events"]
            for event in events[delivered:]:
                visible_event = event
                if principal.role == "user":
                    visible_event = {
                        "stage": "completed" if event.get("stage") == "completed" else "processing",
                        "message": "答复已完成" if event.get("stage") == "completed" else "正在处理，请稍候",
                        "at": event.get("at"),
                    }
                yield f"data: {json.dumps(visible_event, ensure_ascii=False)}\n\n"
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
async def search(
    request: SearchRequest,
    _: Annotated[Principal, Depends(require_admin)],
) -> list[dict]:
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
async def evidence(
    evidence_id: str,
    principal: Annotated[Principal, Depends(current_user)],
) -> dict:
    if principal.role != "admin" and not (
        database.user_answer_references(principal.user_id, "sourceId", evidence_id)
        or database.user_answer_references(principal.user_id, "evidenceId", evidence_id)
    ):
        raise HTTPException(status_code=403, detail="该证据未被你的问答结果引用")
    item = database.get_passage(evidence_id)
    if not item:
        raise HTTPException(status_code=404, detail="证据不存在")
    item["pdfUrl"] = f"/api/documents/{item['document_id']}/file#page={item['page']}"
    return item


@app.get("/api/documents", response_model=list[DocumentOut])
async def documents(_: Annotated[Principal, Depends(require_admin)]) -> list[DocumentOut]:
    return [DocumentOut.model_validate(item) for item in database.list_documents()]


@app.get("/api/documents/{document_id}/file")
async def document_file(
    document_id: str,
    principal: Annotated[Principal, Depends(current_user)],
) -> FileResponse:
    if principal.role != "admin" and not database.user_answer_references(
        principal.user_id, "documentId", document_id
    ):
        raise HTTPException(status_code=403, detail="该文件未被你的问答结果引用")
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
    _: Annotated[Principal, Depends(require_admin)],
) -> DocumentOut:
    try:
        document = await asyncio.to_thread(ingest_document, database, spec, settings.source_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DocumentOut.model_validate(document)


@app.get("/api/documents/{document_id}/versions")
async def document_versions(
    document_id: str,
    _: Annotated[Principal, Depends(require_admin)],
) -> list[dict]:
    versions = database.version_chain(document_id)
    if not versions:
        raise HTTPException(status_code=404, detail="法规文件不存在")
    return versions


@app.post("/api/admin/seed")
async def seed_documents(_: Annotated[Principal, Depends(require_admin)]) -> dict:
    results = await asyncio.to_thread(seed, database)
    return {"count": len(results), "results": results}


@app.post("/api/admin/sync-local")
async def sync_local_documents(_: Annotated[Principal, Depends(require_admin)]) -> dict:
    return await asyncio.to_thread(sync_local_library, database)


frontend_dist = PROJECT_DIR / "frontend" / "dist"
if frontend_dist.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
