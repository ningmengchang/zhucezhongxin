from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timezone
from typing import Any

from pydantic import ValidationError

from .codex_runner import CodexError, CodexRunner, LawToolContext
from .database import Database
from .models import JobStatus, MarkdownLegalAnswer, QuestionRequest, ReportSource, ResearchTraceItem
from .retrieval import Retriever
from .web_evidence import WebEvidenceValidator


class QAService:
    def __init__(self, database: Database, retriever: Retriever, runner: CodexRunner, queue_limit: int):
        self.database = database
        self.retriever = retriever
        self.runner = runner
        self.web_evidence = WebEvidenceValidator()
        self.queue: asyncio.Queue[tuple[str, QuestionRequest, str | None]] = asyncio.Queue(
            maxsize=queue_limit
        )
        self.workers: list[asyncio.Task] = []

    async def start(self, worker_count: int) -> None:
        if self.workers:
            return
        self.workers = [asyncio.create_task(self._worker(), name=f"qa-worker-{i}") for i in range(worker_count)]

    async def stop(self) -> None:
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()

    async def submit(
        self,
        request: QuestionRequest,
        user_id: str | None = None,
        session_key: str | None = None,
    ) -> str:
        if self.queue.full():
            raise RuntimeError("问答队列已满，请稍后重试")
        job_id = f"JOB-{uuid.uuid4().hex[:16].upper()}"
        self.database.create_job(
            job_id,
            request.question,
            request.conversation_id,
            request.as_of_date.isoformat() if request.as_of_date else None,
            user_id,
        )
        await self.queue.put((job_id, request, session_key))
        return job_id

    async def close_session(self, session_key: str) -> None:
        await self.runner.close_session(session_key)

    async def _worker(self) -> None:
        while True:
            job_id, request, session_key = await self.queue.get()
            try:
                await self._process(job_id, request, session_key)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.database.update_job(
                    job_id,
                    status=JobStatus.failed.value,
                    error=str(exc),
                    event=self._event("failed", str(exc)),
                )
            finally:
                self.queue.task_done()

    async def _process(
        self, job_id: str, request: QuestionRequest, session_key: str | None = None
    ) -> None:
        await self._process_tool_agent(job_id, request, session_key)

    async def _process_tool_agent(
        self, job_id: str, request: QuestionRequest, session_key: str | None
    ) -> None:
        deadline = asyncio.get_running_loop().time() + self.runner.settings.research_total_timeout_seconds
        effective_on = request.as_of_date or date.today()
        self.database.update_job(
            job_id,
            status=JobStatus.retrieving.value,
            event=self._event(
                "retrieving",
                "Codex已获得本地搜索、原文读取和Web搜索能力，正在自主研究并生成答复",
            ),
        )
        result = await self._run_before_deadline(
            deadline,
            self._autonomous_agent_prompt(request),
            "markdown_answer.schema.json",
            allow_web_search=True,
            law_tools=LawToolContext(
                database_path=self.runner.settings.database_path,
                as_of_date=effective_on,
                include_future_rules=request.include_future_rules,
            ),
            session_key=session_key,
        )
        evidence, trace = self._evidence_from_law_trace(
            result.law_trace,
            effective_on,
            invocation_started_at=result.started_at,
        )
        trace.extend(self._web_trace_from_events(result.events))
        if trace and result.finished_at:
            last_finished = next(
                (item.finishedAt for item in reversed(trace) if item.finishedAt),
                result.started_at,
            )
            if last_finished:
                trace[-1].finalAnswerMs = round(
                    max(0.0, (result.finished_at - last_finished).total_seconds() * 1000), 3
                )

        try:
            answer = MarkdownLegalAnswer.model_validate(result.payload)
        except ValidationError as exc:
            raise CodexError(f"Codex答复未通过应用层Schema校验: {exc}") from exc
        web_sources = []
        if answer.webSources:
            remaining = self._remaining_seconds(deadline)
            if remaining > 0:
                try:
                    web_sources, _ = await asyncio.wait_for(
                        self.web_evidence.validate_many(
                            [item.model_dump(mode="json") for item in answer.webSources]
                        ),
                        timeout=min(remaining, 60),
                    )
                except asyncio.TimeoutError:
                    web_sources = []
        answer.sources = self._report_sources(evidence, web_sources)
        answer.researchTrace = trace
        if not answer.sources:
            answer.status = "unresolved"
            answer.markdown = (
                "未在资料库或可核验的外部来源中找到足够依据，暂时无法给出可靠结论。"
                "请补充具体法规领域、产品类型、业务页面或相关文件后再试。"
            )

        self.database.update_job(
            job_id,
            status=JobStatus.completed.value,
            answer=answer.model_dump(mode="json"),
            event=self._event("completed", "Codex自主研究和原生Markdown答复生成完成"),
        )

    @staticmethod
    def _remaining_seconds(deadline: float) -> float:
        return max(0.0, deadline - asyncio.get_running_loop().time())

    async def _run_before_deadline(
        self,
        deadline: float,
        prompt: str,
        schema_name: str,
        *,
        allow_web_search: bool = False,
        law_tools: LawToolContext | None = None,
        session_key: str | None = None,
    ):
        remaining = self._remaining_seconds(deadline)
        if remaining <= 0:
            raise asyncio.TimeoutError
        return await asyncio.wait_for(
            self.runner.run(
                prompt,
                schema_name,
                allow_web_search=allow_web_search,
                law_tools=law_tools,
                session_key=session_key,
            ),
            timeout=remaining,
        )

    def _evidence_from_law_trace(
        self,
        tool_trace: list[dict[str, Any]],
        effective_on: date,
        *,
        invocation_started_at: datetime | None = None,
        invocation_finished_at: datetime | None = None,
    ) -> tuple[list[Any], list[ResearchTraceItem]]:
        evidence_by_id: dict[str, Any] = {}
        trace: list[ResearchTraceItem] = []
        search_round = 0
        read_round = 0
        previous_finished_at = invocation_started_at

        def timestamp(value: Any) -> datetime | None:
            if isinstance(value, datetime):
                return value
            if not value:
                return None
            try:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return None

        for event in tool_trace:
            action = event.get("action")
            started_at = timestamp(event.get("startedAt"))
            finished_at = timestamp(event.get("finishedAt"))
            model_thinking_ms = None
            if started_at and previous_finished_at:
                model_thinking_ms = round(
                    max(0.0, (started_at - previous_finished_at).total_seconds() * 1000), 3
                )
            duration_ms = event.get("durationMs")
            if not isinstance(duration_ms, (int, float)):
                duration_ms = None
            if action == "search":
                search_round += 1
                query = str(event.get("query", "")).strip()
                trace.append(
                    ResearchTraceItem(
                        round=search_round,
                        type="local",
                        action="search",
                        queries=[query] if query else [],
                        resultCount=int(event.get("resultCount", 0) or 0),
                        decision="SearchLaw完成候选定位",
                        startedAt=started_at,
                        finishedAt=finished_at,
                        durationMs=duration_ms,
                        modelThinkingMs=model_thinking_ms,
                    )
                )
            elif action == "read":
                read_round += 1
                added = 0
                for source_id in event.get("resultIds", []):
                    if source_id in evidence_by_id:
                        continue
                    passages = self.retriever.read_source(
                        str(source_id), context_chunks=0, as_of_date=effective_on
                    )
                    if passages:
                        evidence_by_id[source_id] = passages[0]
                        added += 1
                trace.append(
                    ResearchTraceItem(
                        round=read_round,
                        type="local",
                        action="read",
                        queries=[str(event.get("requestedId", ""))],
                        resultCount=added,
                        decision="ReadLawSource完成原文读取",
                        startedAt=started_at,
                        finishedAt=finished_at,
                        durationMs=duration_ms,
                        modelThinkingMs=model_thinking_ms,
                    )
                )
            if finished_at:
                previous_finished_at = finished_at

        if trace and invocation_finished_at and previous_finished_at:
            trace[-1].finalAnswerMs = round(
                max(0.0, (invocation_finished_at - previous_finished_at).total_seconds() * 1000),
                3,
            )
        return list(evidence_by_id.values()), trace

    @staticmethod
    def _web_trace_from_events(events: list[dict[str, Any]]) -> list[ResearchTraceItem]:
        traces: list[ResearchTraceItem] = []
        seen: set[str] = set()
        for event in events:
            item = event.get("item")
            item_type = str(item.get("type", "")) if isinstance(item, dict) else ""
            if not isinstance(item, dict) or item_type not in {"webSearch", "web_search_call"}:
                continue
            identifier = str(item.get("id") or "")
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            query = str(item.get("query") or action.get("query") or item.get("text") or "").strip()
            key = identifier or f"{item.get('type')}:{query}"
            if key in seen:
                continue
            seen.add(key)
            results = item.get("results")
            traces.append(
                ResearchTraceItem(
                    round=len(traces) + 1,
                    type="web",
                    queries=[query] if query else [],
                    resultCount=len(results) if isinstance(results, list) else 0,
                    decision="Web Search已调用",
                )
            )
        return traces

    @staticmethod
    def _autonomous_agent_prompt(request: QuestionRequest) -> str:
        return f"""请回答用户问题。你可以使用SearchLaw搜索本地文件、使用ReadLawSource读取本地原文，也可以使用Web Search；使用哪些关键词、调用顺序、调用次数以及如何组织答案，均由你自行判断。

多语言检索原则：
- 本地资料可能使用与用户问题不同的语言。先判断问题所属法规领域，再由你自行识别原文中的标准法规术语、可能的语言、英美拼写变体、缩写和相关概念。
- 用户使用非英文提问时，不要只搜索直译后的整句。将用户原词与标准英文术语、拼写变体和相关概念分别作为聚焦查询提交；SearchLaw不会替你翻译或扩展关键词。
- 阅读候选摘要后判断相关性；结果不充分时自行调整关键词继续搜索。准备在答案中采用任何本地候选前，必须调用ReadLawSource读取并核对原文。
- 同一术语在资料中存在多个定义或适用场景时，应根据实际读取的内容分别说明，不要擅自收窄为一种含义。

资料使用原则：
- 本地资料库中的法规、用户指南、标准、草案、工作文件和说明材料，在检索、阅读、分析和形成结论时具有相同优先级。只按与用户问题的相关性选材，不因正式性、生效状态、文件类型、等级或审核状态优先或排除任何文件。
- 不得默认把用户问题改写成“正式法规能够确认什么”或“法规层面能够确认什么”，也不得以正式法规的范围作为答案范围。只有用户明确询问法律效力、法定义务或生效状态时，才以正式性或生效状态作为分析重点。
- 用户要求“全部”“所有”“完整列出”时，应面向全部本地资料主动检索不同表述和相关文件，合并法规、指南、标准、草案及说明材料中的相关内容，给出尽可能完整的结果。
- 可以中性标明信息来自哪个文件，以及属于法规、指南、标准、草案或其他本地资料，但文件性质只用于说明来源，不代表高低权重；无法判断性质时直接标为“本地资料”。

证据原则：
- 实质性结论必须建立在已通过ReadLawSource读取的本地原文，或能够逐字核验的外部页面原文之上。搜索候选摘要、一般知识和未经核验的链接不能单独支撑结论。
- 本地资料不足时可以使用Web Search补充，优先选择官方、原始发布或其他可靠来源。准备采用外部来源时，在webSources中返回标题、URL、发布方、逐字引文以及可确认的日期；没有外部来源则返回空数组。
- 如果最终没有任何实际读取的本地来源，也没有可核验的外部来源，将status设为unresolved，直接说明依据不足，不得给出通用推测答案。

最终正文放入markdown字段；内容结构、格式、视角和篇幅由你自行决定。status由你根据最终回答自行选择。
最终正文只呈现对用户问题的实质回答和必要依据，不得叙述模型、工具名称、搜索轮次、调用过程、系统架构或其他内部实现。即使使用了本地搜索、原文读取或Web Search，也不要在正文中说明这些处理过程。

回答时点：{(request.as_of_date or date.today()).isoformat()}
是否纳入已公布待生效文件：{request.include_future_rules}
用户问题：{request.question}
"""

    @staticmethod
    def _report_sources(evidence: list[Any], web_sources: list[ReportSource]) -> list[ReportSource]:
        sources: list[ReportSource] = []
        for item in evidence:
            sources.append(
                ReportSource(
                    sourceId=item.evidenceId,
                    origin="local",
                    authorityKind="local_supplement",
                    title=item.documentTitle,
                    documentId=item.documentId,
                    filename=item.filename,
                    article=item.article,
                    page=item.page,
                    quote=item.quote,
                    url=item.officialUrl,
                    publicationDate=item.publicationDate,
                    effectiveDate=item.effectiveDate,
                    applicabilityStatus=item.applicabilityStatus,
                    verified=True,
                )
            )
        sources.extend(web_sources)
        return sources

    @staticmethod
    def _event(stage: str, message: str) -> dict[str, Any]:
        return {
            "stage": stage,
            "message": message,
            "at": datetime.now(timezone.utc).isoformat(),
        }
