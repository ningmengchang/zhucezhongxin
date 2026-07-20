from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any

from pydantic import ValidationError

from .codex_runner import CodexError, CodexRunner, LawToolContext
from .database import Database
from .models import Claim, JobStatus, LegalAnswer, QuestionRequest, TimeContext, TimeNotice
from .models import (
    QualityNoteV4,
    ReportParagraph,
    ReportSource,
    ResearchReport,
    ResearchTraceItem,
    WorkProductReport,
)
from .retrieval import Retriever
from .web_evidence import WebEvidenceValidator


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


WORK_BLOCK_TYPES = {
    "deep_analysis",
    "comparison_table",
    "field_matrix",
    "responsibility_matrix",
    "process",
    "checklist",
    "decision_rules",
    "example",
    "common_mistakes",
}


class QAService:
    def __init__(self, database: Database, retriever: Retriever, runner: CodexRunner, queue_limit: int):
        self.database = database
        self.retriever = retriever
        self.runner = runner
        self.queue: asyncio.Queue[tuple[str, QuestionRequest]] = asyncio.Queue(maxsize=queue_limit)
        self.workers: list[asyncio.Task] = []
        self.web_validator = WebEvidenceValidator(
            runner.settings.web_official_domains,
            runner.settings.web_max_sources,
        )

    async def start(self, worker_count: int) -> None:
        if self.workers:
            return
        self.workers = [asyncio.create_task(self._worker(), name=f"qa-worker-{i}") for i in range(worker_count)]

    async def stop(self) -> None:
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()

    async def submit(self, request: QuestionRequest) -> str:
        if self.queue.full():
            raise RuntimeError("问答队列已满，请稍后重试")
        job_id = f"JOB-{uuid.uuid4().hex[:16].upper()}"
        self.database.create_job(
            job_id,
            request.question,
            request.conversation_id,
            request.as_of_date.isoformat() if request.as_of_date else None,
        )
        await self.queue.put((job_id, request))
        return job_id

    async def _worker(self) -> None:
        while True:
            job_id, request = await self.queue.get()
            try:
                await self._process(job_id, request)
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

    async def _process(self, job_id: str, request: QuestionRequest) -> None:
        if self.runner.settings.law_retrieval_mode == "legacy":
            await self._process_legacy(job_id, request)
            return
        await self._process_tool_agent(job_id, request)

    async def _process_tool_agent(self, job_id: str, request: QuestionRequest) -> None:
        deadline = asyncio.get_running_loop().time() + self.runner.settings.research_total_timeout_seconds
        history = self.database.conversation_history(
            request.conversation_id,
            exclude_job_id=job_id,
            limit=self.runner.settings.conversation_memory_turns,
        )
        effective_on = request.as_of_date or date.today()
        self.database.update_job(
            job_id,
            status=JobStatus.retrieving.value,
            event=self._event(
                "retrieving",
                "Codex正在使用SearchLaw定位法规，并按需调用ReadLawSource读取原文"
                + (f"；已载入{len(history)}轮会话记忆" if history else ""),
            ),
        )
        local_result = await self._run_before_deadline(
            deadline,
            self._local_agent_prompt(request, history),
            "local_law_agent.schema.json",
            law_tools=LawToolContext(
                database_path=self.runner.settings.database_path,
                as_of_date=effective_on,
                include_future_rules=request.include_future_rules,
            ),
        )
        payload = local_result.payload
        decision = payload.get("decision")
        if decision not in {"sufficient", "needs_web", "unresolved"}:
            decision = "unresolved"
        profile = self._merge_question_profile(request.question, payload.get("questionProfile", {}))
        assessment = {
            "decision": "search_web" if decision == "needs_web" else decision,
            "reason": str(payload.get("reason", "")).strip(),
            "gaps": [str(item) for item in payload.get("criticalGaps", [])],
            "criticalGaps": [str(item) for item in payload.get("criticalGaps", [])],
            "nextQueries": [],
            "webQueries": [str(item) for item in payload.get("webQueries", [])][:4],
            "webRequired": decision == "needs_web",
            "questionProfile": profile,
        }
        evidence, trace = self._evidence_from_law_trace(
            local_result.law_trace,
            effective_on,
            invocation_started_at=local_result.started_at,
            invocation_finished_at=local_result.finished_at,
        )
        if not any(item.action == "search" for item in trace) and not self.runner.settings.codex_mock:
            raise CodexError("法规Agent未调用SearchLaw，已拒绝无检索依据的答案")
        if not evidence:
            decision = "unresolved"
            assessment["decision"] = "unresolved"
            assessment["criticalGaps"] = ["没有通过ReadLawSource读取可引用的本地法规原文"]

        try:
            answer = WorkProductReport.model_validate(payload.get("report", {}))
        except ValidationError as exc:
            raise CodexError(f"Codex本地法规Agent未通过应用层Schema校验: {exc}") from exc
        local_sources = self._report_sources(evidence, [])
        answer.sources = local_sources
        answer.researchTrace = trace
        answer.timeContext = self._time_context(request, evidence)
        answer = self._validate_work_product(answer, profile)

        explicit_web_request = self._explicit_web_requested(request.question)
        web_sources: list[ReportSource] = []
        rejected_web: list[str] = []
        if (
            (explicit_web_request or decision == "needs_web")
            and self.runner.settings.web_search_enabled
            and self.runner.settings.research_web_rounds > 0
            and self._remaining_seconds(deadline) > 90
        ):
            web_queries = assessment["webQueries"] or [request.question]
            web_result = None
            self.database.update_job(
                job_id,
                status=JobStatus.web_searching.value,
                event=self._event("web_searching", "本地原文仍有关键缺口，正在检索并核验欧盟官方网站"),
            )
            try:
                web_result = await self._run_before_deadline(
                    deadline,
                    self._web_prompt(request, assessment, web_queries),
                    "web_research.schema.json",
                    allow_web_search=True,
                )
                used_native_search = any(
                    isinstance(event.get("item"), dict)
                    and "web_search" in str(event["item"].get("type", ""))
                    for event in web_result.events
                )
                if used_native_search or self.runner.settings.codex_mock:
                    web_sources, rejected_web = await asyncio.wait_for(
                        self.web_validator.validate_many(web_result.payload.get("sources", [])),
                        timeout=max(1.0, self._remaining_seconds(deadline)),
                    )
                else:
                    rejected_web = ["Codex未执行原生Web Search，本轮结果已丢弃"]
            except asyncio.TimeoutError:
                rejected_web = ["官方Web研究达到时间上限，未保留未经完整校验的结果"]
            answer.researchTrace.append(
                ResearchTraceItem(
                    round=1,
                    type="web",
                    queries=web_queries,
                    resultCount=len(web_sources),
                    decision=(
                        f"核验通过{len(web_sources)}个欧盟官方来源"
                        if web_sources
                        else "未找到可核验的欧盟官方来源"
                    ),
                    startedAt=web_result.started_at if web_result else None,
                    finishedAt=web_result.finished_at if web_result else None,
                    durationMs=web_result.duration_ms if web_result else None,
                )
            )

        all_sources = self._report_sources(evidence, web_sources)
        if web_sources:
            completed_trace = list(answer.researchTrace)
            self.database.update_job(
                job_id,
                status=JobStatus.generating.value,
                event=self._event("generating", "正在结合已读取本地原文和已核验Web来源生成最终答案"),
            )
            generation = await self._run_before_deadline(
                deadline,
                self._report_prompt(request, all_sources, assessment, rejected_web, history),
                "work_product_report.schema.json",
            )
            answer = WorkProductReport.model_validate(generation.payload)
            answer.sources = all_sources
            answer.researchTrace = completed_trace
            answer.timeContext = self._time_context(request, evidence)
            answer = self._validate_work_product(answer, profile)
        elif decision in {"needs_web", "unresolved"} and answer.status == "answered":
            answer.status = "partial"

        quality_gaps = self._work_product_quality_gaps(answer, profile)
        if quality_gaps and self.runner.settings.report_expansion_enabled and self._remaining_seconds(deadline) > 90:
            initial_answer = answer.model_copy(deep=True)
            self.database.update_job(
                job_id,
                status=JobStatus.generating.value,
                event=self._event("generating", "首稿缺少直接结论或详细分析，正在定向修订"),
            )
            try:
                expanded = await self._run_before_deadline(
                    deadline,
                    self._expansion_prompt(request, answer, all_sources, assessment, quality_gaps),
                    "work_product_report.schema.json",
                )
                candidate = WorkProductReport.model_validate(expanded.payload)
                candidate.sources = all_sources
                candidate.researchTrace = answer.researchTrace
                candidate.timeContext = answer.timeContext
                candidate = self._validate_work_product(candidate, profile)
                candidate_gaps = self._work_product_quality_gaps(candidate, profile)
                if len(candidate_gaps) < len(quality_gaps):
                    answer = candidate
            except (asyncio.TimeoutError, ValidationError):
                answer = initial_answer

        if self.runner.settings.codex_verify_enabled and self._work_product_item_count(answer):
            self.database.update_job(
                job_id,
                status=JobStatus.verifying.value,
                event=self._event("verifying", "正在独立审核报告覆盖范围和全部来源引用"),
            )
            try:
                verification = await self._run_before_deadline(
                    deadline,
                    self._work_product_verification_prompt(request, answer, profile),
                    "work_product_verification.schema.json",
                )
                answer = self._apply_work_product_verification(answer, verification.payload)
                answer.researchTrace.append(
                    ResearchTraceItem(
                        round=1,
                        type="verification",
                        queries=[],
                        resultCount=self._work_product_item_count(answer),
                        decision=str(verification.payload.get("reviewNote", "独立审核完成")),
                        startedAt=verification.started_at,
                        finishedAt=verification.finished_at,
                        durationMs=verification.duration_ms,
                    )
                )
                answer = self._validate_work_product(answer, profile)
            except asyncio.TimeoutError:
                answer.status = "partial"
                answer.qualityNotes.append(
                    QualityNoteV4(
                        noteId="QN-VERIFY-TIMEOUT",
                        type="system",
                        message="独立审核达到时间上限；内容已通过确定性来源校验，但未完成模型完整性复核",
                    )
                )

        self.database.update_job(
            job_id,
            status=JobStatus.completed.value,
            answer=answer.model_dump(mode="json"),
            event=self._event("completed", "按需检索、原文阅读和报告审核完成"),
        )

    async def _process_legacy(self, job_id: str, request: QuestionRequest) -> None:
        deadline = asyncio.get_running_loop().time() + self.runner.settings.research_total_timeout_seconds
        history = self.database.conversation_history(
            request.conversation_id,
            exclude_job_id=job_id,
            limit=self.runner.settings.conversation_memory_turns,
        )
        context_queries = self._retrieval_context_queries(history)
        accumulated: dict[str, Any] = {}
        trace: list[ResearchTraceItem] = []
        next_queries = [request.question]
        seen_queries: set[str] = set()
        initial_profile = self._default_question_profile(request.question)
        configured_rounds = max(1, self.runner.settings.research_local_rounds)
        adaptive_round_limit = self._adaptive_local_round_limit(initial_profile, configured_rounds)
        local_search_exhausted = False
        assessment: dict[str, Any] = {
            "decision": "search_local",
            "reason": "尚未开始研究",
            "coveredTopics": [],
            "gaps": [request.question],
            "criticalGaps": [],
            "nextQueries": [],
            "webQueries": [],
            "webRequired": False,
            "questionProfile": initial_profile,
        }

        for round_number in range(1, configured_rounds + 1):
            if round_number > adaptive_round_limit:
                local_search_exhausted = True
                break
            current_queries = self._unique_queries(next_queries)
            if not current_queries:
                local_search_exhausted = True
                break
            seen_queries.update(_normalize(query) for query in current_queries)
            self.database.update_job(
                job_id,
                status=JobStatus.retrieving.value,
                event=self._event(
                    "retrieving",
                    f"自适应本地检索第{round_number}轮（当前最多{adaptive_round_limit}轮）"
                    + (f"；已载入{len(history)}轮会话记忆" if round_number == 1 and history else ""),
                ),
            )
            found = self._search_local(current_queries, request, context_queries if round_number == 1 else [])
            before = len(accumulated)
            for item in found:
                if len(accumulated) >= self.runner.settings.research_max_evidence:
                    break
                accumulated.setdefault(item.evidenceId, item)
            added = len(accumulated) - before
            evidence = list(accumulated.values())

            if round_number > 1 and added == 0:
                local_search_exhausted = True
                trace.append(
                    ResearchTraceItem(
                        round=round_number,
                        type="local",
                        queries=current_queries,
                        resultCount=0,
                        decision="本轮没有新增有效证据，停止本地扩检",
                    )
                )
                break

            self.database.update_job(
                job_id,
                status=JobStatus.assessing.value,
                event=self._event("assessing", f"新增{added}条、累计{len(evidence)}条证据，正在判断覆盖范围"),
            )
            try:
                result = await self._run_before_deadline(
                    deadline,
                    self._assessment_prompt(request, evidence, assessment.get("gaps", []), history),
                    "research_assessment.schema.json",
                )
            except asyncio.TimeoutError:
                assessment = {
                    "decision": "unresolved",
                    "reason": "研究达到时间上限，停止扩大检索",
                    "coveredTopics": assessment.get("coveredTopics", []),
                    "gaps": assessment.get("gaps", [request.question]),
                    "criticalGaps": assessment.get("criticalGaps", []),
                    "nextQueries": [],
                    "webQueries": [],
                    "webRequired": assessment.get("webRequired", False),
                    "questionProfile": assessment.get("questionProfile", self._default_question_profile(request.question)),
                }
                trace.append(
                    ResearchTraceItem(
                        round=round_number,
                        type="local",
                        queries=current_queries,
                        resultCount=added,
                        decision="研究达到时间上限，停止扩大检索",
                    )
                )
                break
            assessment = self._normalize_assessment(result.payload)
            assessment["questionProfile"] = self._merge_question_profile(
                request.question, assessment.get("questionProfile", {})
            )
            trace.append(
                ResearchTraceItem(
                    round=round_number,
                    type="local",
                    queries=current_queries,
                    resultCount=added,
                    decision=self._assessment_decision_label(assessment),
                )
            )
            if assessment["decision"] == "sufficient":
                break
            proposed = [
                query
                for query in self._unique_queries(assessment.get("nextQueries") or [])
                if _normalize(query) not in seen_queries
            ]
            if not proposed:
                local_search_exhausted = True
                break
            if round_number >= adaptive_round_limit:
                local_search_exhausted = True
                break
            if round_number >= 2 and not assessment.get("criticalGaps"):
                local_search_exhausted = True
                break
            next_queries = proposed[:6]

        evidence = list(accumulated.values())
        web_sources: list[ReportSource] = []
        rejected_web: list[str] = []
        explicit_web_request = self._explicit_web_requested(request.question)
        needs_web = self._should_search_web(
            assessment,
            explicit_request=explicit_web_request,
            local_search_exhausted=local_search_exhausted,
        )
        if (
            needs_web
            and self.runner.settings.web_search_enabled
            and self.runner.settings.research_web_rounds > 0
            and self._remaining_seconds(deadline) > 90
        ):
            web_queries = (assessment.get("webQueries") or assessment.get("nextQueries") or [request.question])[:4]
            web_result = None
            self.database.update_job(
                job_id,
                status=JobStatus.web_searching.value,
                event=self._event(
                    "web_searching",
                    "用户明确要求联网，正在检索欧盟官方网站"
                    if explicit_web_request
                    else "关键法规依据仍缺失，正在检索欧盟官方网站",
                ),
            )
            try:
                web_result = await self._run_before_deadline(
                    deadline,
                    self._web_prompt(request, assessment, web_queries),
                    "web_research.schema.json",
                    allow_web_search=True,
                )
                used_native_search = any(
                    isinstance(event.get("item"), dict)
                    and "web_search" in str(event["item"].get("type", ""))
                    for event in web_result.events
                )
                if used_native_search or self.runner.settings.codex_mock:
                    web_sources, rejected_web = await asyncio.wait_for(
                        self.web_validator.validate_many(web_result.payload.get("sources", [])),
                        timeout=max(1.0, self._remaining_seconds(deadline)),
                    )
                else:
                    rejected_web = ["Codex未执行原生Web Search，本轮结果已丢弃"]
            except asyncio.TimeoutError:
                rejected_web = ["官方Web研究达到时间上限，未保留未经完整校验的结果"]
            trace.append(
                ResearchTraceItem(
                    round=1,
                    type="web",
                    queries=web_queries,
                    resultCount=len(web_sources),
                    decision=(
                        f"核验通过{len(web_sources)}个欧盟官方来源"
                        if web_sources
                        else "未找到可核验的欧盟官方来源"
                    ),
                    startedAt=web_result.started_at if web_result else None,
                    finishedAt=web_result.finished_at if web_result else None,
                    durationMs=web_result.duration_ms if web_result else None,
                )
            )

        all_sources = self._report_sources(evidence, web_sources)
        self.database.update_job(
            job_id,
            status=JobStatus.generating.value,
            event=self._event("generating", f"正在使用{len(all_sources)}个有效来源生成法规答复"),
        )
        try:
            generation = await self._run_before_deadline(
                deadline,
                self._report_prompt(request, all_sources, assessment, rejected_web, history),
                "work_product_report.schema.json",
            )
        except asyncio.TimeoutError:
            answer = WorkProductReport(
                status="unresolved",
                answerTitle="研究达到时间上限",
                timeContext=self._time_context(request, evidence),
                unresolvedQuestions=[
                    {
                        "itemId": "U-TIMEOUT",
                        "question": "本轮研究未能在时间上限内完成",
                        "impact": "系统未输出未经完整生成和审核的半成品结论",
                        "neededEvidence": "重新提问或缩小问题范围后重试",
                    }
                ],
                researchTrace=trace,
            )
            self.database.update_job(
                job_id,
                status=JobStatus.completed.value,
                answer=answer.model_dump(mode="json"),
                event=self._event("completed", "研究达到时间上限，已安全返回未解决状态"),
            )
            return
        try:
            answer = WorkProductReport.model_validate(generation.payload)
        except ValidationError as exc:
            raise CodexError(f"Codex法规答复未通过应用层Schema校验: {exc}") from exc
        answer.timeContext = self._time_context(request, evidence)
        answer.sources = all_sources
        answer.researchTrace = trace
        answer = self._validate_work_product(answer, assessment.get("questionProfile", {}))

        quality_gaps = self._work_product_quality_gaps(answer, assessment.get("questionProfile", {}))
        if (
            quality_gaps
            and self.runner.settings.report_expansion_enabled
            and self._remaining_seconds(deadline) > 90
        ):
            initial_answer = answer.model_copy(deep=True)
            self.database.update_job(
                job_id,
                status=JobStatus.generating.value,
                event=self._event("generating", "首稿缺少直接结论或详细分析，正在定向修订"),
            )
            try:
                expanded = await self._run_before_deadline(
                    deadline,
                    self._expansion_prompt(request, answer, all_sources, assessment, quality_gaps),
                    "work_product_report.schema.json",
                )
                candidate = WorkProductReport.model_validate(expanded.payload)
                candidate.timeContext = self._time_context(request, evidence)
                candidate.sources = all_sources
                candidate.researchTrace = trace
                candidate = self._validate_work_product(
                    candidate, assessment.get("questionProfile", {})
                )
                candidate_gaps = self._work_product_quality_gaps(
                    candidate, assessment.get("questionProfile", {})
                )
                if len(candidate_gaps) < len(quality_gaps):
                    answer = candidate
                else:
                    answer = initial_answer
                    answer.qualityNotes.append(
                        QualityNoteV4(
                            noteId="QN-EXPAND-NO-GAIN",
                            type="system",
                            message="定向修订未补齐明确缺口，已保留首稿",
                        )
                    )
            except (asyncio.TimeoutError, ValidationError):
                answer.qualityNotes.append(
                    QualityNoteV4(
                        noteId="QN-EXPAND",
                        type="system",
                        message="定向扩写未能在时间或结构约束内完成，已保留通过校验的首稿",
                    )
                )

        if self.runner.settings.codex_verify_enabled and self._work_product_item_count(answer):
            self.database.update_job(
                job_id,
                status=JobStatus.verifying.value,
                event=self._event("verifying", "正在独立审核报告覆盖范围和全部来源引用"),
            )
            try:
                verification = await self._run_before_deadline(
                    deadline,
                    self._work_product_verification_prompt(request, answer, assessment.get("questionProfile", {})),
                    "work_product_verification.schema.json",
                )
                answer = self._apply_work_product_verification(answer, verification.payload)
                answer.researchTrace.append(
                    ResearchTraceItem(
                        round=1,
                        type="verification",
                        queries=[],
                        resultCount=self._work_product_item_count(answer),
                        decision=str(verification.payload.get("reviewNote", "独立审核完成")),
                        startedAt=verification.started_at,
                        finishedAt=verification.finished_at,
                        durationMs=verification.duration_ms,
                    )
                )
                answer = self._validate_work_product(
                    answer, assessment.get("questionProfile", {})
                )
            except asyncio.TimeoutError:
                answer.status = "partial"
                answer.qualityNotes.append(
                    QualityNoteV4(
                        noteId="QN-VERIFY-TIMEOUT",
                        type="system",
                        message="独立审核达到时间上限；内容已通过确定性来源校验，但未完成模型完整性复核",
                    )
                )

        self.database.update_job(
            job_id,
            status=JobStatus.completed.value,
            answer=answer.model_dump(mode="json"),
            event=self._event("completed", "迭代研究、来源校验和报告审核完成"),
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
            if action in {"search", "search_rejected"}:
                search_round += 1
                query = str(event.get("query", "")).strip()
                trace.append(
                    ResearchTraceItem(
                        round=search_round,
                        type="local",
                        action="search",
                        queries=[query] if query else [],
                        resultCount=int(event.get("resultCount", 0) or 0),
                        decision=(
                            "SearchLaw完成候选定位"
                            if action == "search"
                            else "SearchLaw调用已被上限拦截"
                        ),
                        startedAt=started_at,
                        finishedAt=finished_at,
                        durationMs=duration_ms,
                        modelThinkingMs=model_thinking_ms,
                    )
                )
            elif action in {"read", "read_duplicate", "read_rejected"}:
                read_round += 1
                added = 0
                if action == "read":
                    for source_id in event.get("resultIds", []):
                        if source_id in evidence_by_id:
                            continue
                        passages = self.retriever.read_source(
                            str(source_id), context_chunks=0, as_of_date=effective_on
                        )
                        if passages:
                            evidence_by_id[source_id] = passages[0]
                            added += 1
                decision = "ReadLawSource完成法规原文读取"
                if action == "read_duplicate":
                    decision = "重复source_id读取已拦截，复用上一次原文"
                elif action == "read_rejected":
                    decision = "ReadLawSource调用已拒绝"
                trace.append(
                    ResearchTraceItem(
                        round=read_round,
                        type="local",
                        action="read",
                        queries=[str(event.get("requestedId", ""))],
                        resultCount=added,
                        decision=decision,
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
    def _local_agent_prompt(request: QuestionRequest, history: list[dict]) -> str:
        return f"""你是数字产品护照服务商的法规研究Agent。你可以使用本地只读工具SearchLaw和ReadLawSource，并必须在本次任务中完成检索、按需阅读和首稿生成。

研究协议：
1. 必须先调用SearchLaw；先用用户原问题搜索，结果不准时主动改用法规英文术语、条款号、主体和动作继续搜索，最多4次。
2. SearchLaw只用于定位。凡准备作为法规要求、官方解释或标准指导引用的source_id，必须调用ReadLawSource读取原文；最终sourceRefs只能使用ReadLawSource实际返回的source_id。
3. 优先读取最相关的少量来源，必要时读取相邻条款。同一source_id禁止重复调用ReadLawSource，必须复用已返回的原文。不得把搜索摘要、模型记忆或会话历史作为法规证据。
4. 本地证据已覆盖主体、动作、对象、时点、范围和关键例外时decision=sufficient。只有缺少会改变核心结论的正式法规、最新实施文件或官方解释时decision=needs_web，并给不超过4条Web查询。其他不足返回unresolved。
5. 所有本地文件检索权重相同，但必须根据实际原文区分法规、草案、标准、指南和工程建议，不得把标准或建议写成法定义务。

作业包要求：
- 以正文为主：executiveAnswer用1至3条直接回答；再用一个deep_analysis模块充分说明法规依据、适用条件、限制和实际含义。
- keyTakeaways可以为空；只有存在与直接结论不重复的关键决策点时才输出，最多3条。
- contentBlocks最多2个：deep_analysis加一个确实有助于理解的表格、流程或清单。示例、注意事项和限制优先写入正文，不为填充结构拆分模块。
- 服务商行动仅在职责、API/字段、流程或实施类问题中输出，最多3条；概念和定义类问题不生成通用行动项。
- regulatory_requirement、official_interpretation和standard_guidance必须填写已读取的sourceRefs；engineering_recommendation允许无来源但必须明确“建议、非强制”。
- 服务商行动写明owner、priority和deliverable；只有原文直接规定服务商义务时才标direct_service_provider_duty。
- 已公布尚未生效的来源必须说明生效日期。不得创造法规义务、接口契约、字段或source_id。
- 不输出隐藏思维过程，只输出Schema要求的结构化结果。

回答时点：{(request.as_of_date or date.today()).isoformat()}
是否纳入已公布待生效法规：{request.include_future_rules}
用户问题：{request.question}
会话上下文（只用于理解指代）：{json.dumps(QAService._memory_payload(history), ensure_ascii=False)}
"""

    def _search_local(
        self,
        queries: list[str],
        request: QuestionRequest,
        context_queries: list[str],
    ) -> list[Any]:
        merged: dict[str, Any] = {}
        per_query = min(self.runner.settings.max_evidence, 24)
        for query in queries[:6]:
            for item in self.retriever.search(
                query,
                as_of_date=request.as_of_date,
                include_future_rules=request.include_future_rules,
                context_queries=context_queries,
                limit=per_query,
            ):
                previous = merged.get(item.evidenceId)
                if previous is None or item.score > previous.score:
                    merged[item.evidenceId] = item
        return sorted(merged.values(), key=lambda item: item.score, reverse=True)

    @staticmethod
    def _normalize_assessment(payload: dict[str, Any]) -> dict[str, Any]:
        decision = payload.get("decision")
        if decision not in {"sufficient", "search_local", "search_web", "unresolved"}:
            decision = "unresolved"
        raw_profile = payload.get("questionProfile") if isinstance(payload.get("questionProfile"), dict) else {}
        complexity = raw_profile.get("complexity")
        if complexity not in {"simple", "standard", "complex"}:
            complexity = "standard"
        suggested_block_type = str(raw_profile.get("suggestedBlockType", "none"))
        if suggested_block_type not in {*WORK_BLOCK_TYPES, "none"}:
            suggested_block_type = "none"
        profile = {
            "interpretedQuestion": str(raw_profile.get("interpretedQuestion", "")).strip(),
            "complexity": complexity,
            "intents": [str(item).strip() for item in raw_profile.get("intents", []) if str(item).strip()],
            "suggestedBlockType": suggested_block_type,
            "focusAreas": [str(item).strip() for item in raw_profile.get("focusAreas", []) if str(item).strip()],
        }
        return {
            "decision": decision,
            "reason": str(payload.get("reason", "")).strip(),
            "coveredTopics": [str(item).strip() for item in payload.get("coveredTopics", []) if str(item).strip()],
            "gaps": [str(item).strip() for item in payload.get("gaps", []) if str(item).strip()],
            "criticalGaps": [
                str(item).strip() for item in payload.get("criticalGaps", []) if str(item).strip()
            ],
            "nextQueries": [str(item).strip()[:400] for item in payload.get("nextQueries", []) if str(item).strip()],
            "webQueries": [str(item).strip()[:400] for item in payload.get("webQueries", []) if str(item).strip()],
            "webRequired": bool(payload.get("webRequired", False)),
            "questionProfile": profile,
        }

    @staticmethod
    def _unique_queries(queries: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for raw_query in queries:
            query = str(raw_query).strip()[:400]
            normalized = _normalize(query)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(query)
        return unique

    @staticmethod
    def _adaptive_local_round_limit(profile: dict[str, Any], configured_max: int) -> int:
        desired = {"simple": 1, "standard": 2, "complex": 3}.get(
            str(profile.get("complexity", "standard")), 2
        )
        return max(1, min(configured_max, desired))

    @staticmethod
    def _explicit_web_requested(question: str) -> bool:
        normalized = _normalize(question)
        markers = (
            "联网",
            "网络搜索",
            "web搜索",
            "web search",
            "官网查询",
            "官网检索",
            "欧盟检索",
            "eur-lex",
            "最新版本",
            "最新法规",
            "核实最新",
        )
        return any(marker in normalized for marker in markers)

    @staticmethod
    def _should_search_web(
        assessment: dict[str, Any], *, explicit_request: bool, local_search_exhausted: bool
    ) -> bool:
        if explicit_request:
            return True
        if assessment.get("decision") == "sufficient":
            return False
        critical_gaps = assessment.get("criticalGaps") or []
        return bool(critical_gaps) and (
            bool(assessment.get("webRequired")) or local_search_exhausted
        )

    @staticmethod
    def _default_question_profile(question: str) -> dict[str, Any]:
        normalized = question.lower()
        intents: list[str] = []
        if any(marker in normalized for marker in ("区别", "对比", "关系", "不同")):
            intents.append("comparison")
        if any(marker in normalized for marker in ("字段", "入参", "参数", "api", "接口", "编码", "数据结构")):
            intents.append("technical_design")
        if any(marker in question for marker in ("流程", "过程", "如何", "怎么")):
            intents.append("process")
        if any(marker in question for marker in ("职责", "责任", "服务商", "谁")):
            intents.append("responsibility")
        if any(marker in question for marker in ("实现", "落地", "设计", "接入", "开发", "保存", "校验")):
            intents.append("implementation")
        intents = list(dict.fromkeys(intents))
        explicit_subquestions = sum(question.count(marker) for marker in ("？", "?", "；", ";"))
        complex_question = explicit_subquestions >= 2 or (
            len(question) >= 80 and len(intents) >= 2
        )
        complexity = "complex" if complex_question else "standard" if intents else "simple"
        if "technical_design" in intents:
            suggested_block_type = "field_matrix"
        elif "responsibility" in intents:
            suggested_block_type = "responsibility_matrix"
        elif "process" in intents:
            suggested_block_type = "process"
        elif "comparison" in intents:
            suggested_block_type = "comparison_table"
        else:
            suggested_block_type = "none"
        return {
            "interpretedQuestion": question,
            "complexity": complexity,
            "intents": intents or ["legal_explanation"],
            "suggestedBlockType": suggested_block_type,
            "focusAreas": [],
        }

    @staticmethod
    def _merge_question_profile(question: str, model_profile: dict[str, Any]) -> dict[str, Any]:
        fallback = QAService._default_question_profile(question)
        suggested_block_type = fallback["suggestedBlockType"]
        model_suggestion = str(model_profile.get("suggestedBlockType", "none"))
        if suggested_block_type == "none" and model_suggestion in WORK_BLOCK_TYPES:
            suggested_block_type = model_suggestion
        return {
            "interpretedQuestion": model_profile.get("interpretedQuestion") or fallback["interpretedQuestion"],
            "complexity": fallback["complexity"],
            "intents": list(dict.fromkeys([*fallback["intents"], *model_profile.get("intents", [])])),
            "suggestedBlockType": suggested_block_type,
            "focusAreas": model_profile.get("focusAreas", []) or fallback["focusAreas"],
        }

    @staticmethod
    def _assessment_decision_label(assessment: dict[str, Any]) -> str:
        labels = {
            "sufficient": "本地证据已覆盖问题，停止扩检",
            "search_local": "仍有本地检索空间，继续扩大检索",
            "search_web": "本地证据不足，准备查询官方Web来源",
            "unresolved": "当前证据仍不能形成完整结论",
        }
        reason = str(assessment.get("reason", "")).strip()
        return f"{labels.get(assessment.get('decision'), '完成评估')}：{reason}" if reason else labels.get(assessment.get("decision"), "完成评估")

    @staticmethod
    def _assessment_prompt(
        request: QuestionRequest,
        evidence: list[Any],
        previous_gaps: list[str],
        history: list[dict],
    ) -> str:
        return f"""你是法规研究编排器。你的任务不是写最终答案，而是判断累计本地证据是否足以完整回答问题，并为下一轮生成精确检索词。

判断标准：
1. 分解用户问题的全部关键部分；主体、动作、入参、流程、时间效力、例外和技术边界应按实际问题覆盖。
2. 所有本地法规资料按相同证据权重处理；只根据原文是否直接支持结论判断充分性。
3. 不能因为出现一个相关片段就判定充分；但不要把非必要的延伸说明、工程实现细节或锦上添花的信息当成关键缺口。
4. gaps列出所有剩余不足；criticalGaps只列出会改变核心结论、适用主体、法定义务、时间效力或责任边界的缺口。
5. 如果本地可能还有同义词、法规编号、英文术语或相关条款，返回search_local和不超过6条检索词；检索词不得与上一轮重复。
6. webRequired仅在缺少正式法规、最新实施文件或官方解释，且该缺口属于criticalGaps时设为true。一般补充项、工程建议和表述优化不得触发Web。
7. 只有已合理扩大本地检索仍存在criticalGaps时才返回search_web；没有关键缺口时，即使回答可继续丰富，也应返回sufficient。
8. 不使用模型记忆补充法律事实，不输出隐藏思维过程，只给简短评估原因。
9. questionProfile必须结合当前问题和会话指代，明确用户真正想解决的问题、内容意图和关注点。
10. suggestedBlockType只能选一个最能帮助理解的表格、流程或清单类型；不需要结构化内容时返回none。不按字数或模块数判断充分性。

用户问题：{request.question}
上一轮证据缺口：{json.dumps(previous_gaps, ensure_ascii=False)}
会话上下文（只用于理解指代）：{json.dumps(QAService._memory_payload(history), ensure_ascii=False)}
累计本地证据：
{json.dumps([item.model_dump(mode='json') for item in evidence], ensure_ascii=False)}
"""

    def _web_prompt(
        self,
        request: QuestionRequest,
        assessment: dict[str, Any],
        queries: list[str],
    ) -> str:
        domains = ", ".join(self.runner.settings.web_official_domains)
        return f"""你是欧盟法规官方来源研究员。必须使用本轮提供的Web Search工具查询，不得只凭模型记忆回答。

只接受以下官方域名及其子域：{domains}
硬性规则：
1. 优先EUR-Lex上的法规、实施条例、授权条例和正式公报；欧盟机构说明只能标为official_guidance。
2. 不得返回律师事务所、媒体、行业协会、百科或搜索结果页。
3. 每个来源必须给出可直接访问的HTTPS官方URL，以及从该页面逐字复制的连续原文quote。
4. 找不到时sources返回空数组，并在unresolvedGaps说明，不得编造URL、标题、日期或原文。
5. 可以在一个研究轮次中执行多条查询，但只返回与证据缺口直接相关的来源。

回答时点：{(request.as_of_date or date.today()).isoformat()}
用户问题：{request.question}
本地证据缺口：{json.dumps(assessment.get('gaps', []), ensure_ascii=False)}
建议查询：{json.dumps(queries, ensure_ascii=False)}
"""

    @staticmethod
    def _report_sources(evidence: list[Any], web_sources: list[ReportSource]) -> list[ReportSource]:
        sources: list[ReportSource] = []
        for item in evidence:
            sources.append(
                ReportSource(
                    sourceId=item.evidenceId,
                    origin="local",
                    authorityKind="legal_act",
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
    def _report_prompt(
        request: QuestionRequest,
        sources: list[ReportSource],
        assessment: dict[str, Any],
        rejected_web: list[str],
        history: list[dict],
    ) -> str:
        return f"""你是数字产品护照服务商的首席法规与解决方案顾问。根据已验证来源，为用户生成正文优先、内容详实的中文法规答复。

总体要求：
1. 严格按questionProfile理解当前问题，完整覆盖用户明确提出的子问题和focusAreas。不设字数目标，以结论有据、解释充分且不重复为准。
2. executiveAnswer用1至3条直接解决问题。keyTakeaways可以为空；只有存在与executiveAnswer不重复的重要决策点时才输出，最多3条。
3. contentBlocks最多2个：默认使用一个deep_analysis详实解释法规依据、适用条件、限制、例外和实际含义；只在suggestedBlockType确实帮助理解时增加一个结构化块。示例和注意事项优先融入深度分析，不为凑结构创建模块：
   - comparison_table：对比概念、主体、对象、时点、功能、关系和易错点。
   - field_matrix：列出建议字段、业务含义、法规是否直接规定、来源、必填建议、校验和未知项。
   - responsibility_matrix：列出法定主体、直接义务、服务商角色、责任边界和留痕证据。
   - process：每一步填写owner、input、output和验收标准。
   - checklist：给可执行任务、优先级和acceptanceCriteria。
   - example：给示意字段、伪JSON、URI或数据关系示例，同时说明不能直接当作法定格式。
   - decision_rules和common_mistakes：写清何时怎么判断、哪些做法不能采用。
4. 表格块columns给出列名，每个item.cells按相同顺序给单元格；非表格块columns返回空数组。流程和清单项必须使用结构字段，不能把全部内容挤进detail。
5. 每项内容必须准确分类basis：
   - regulatory_requirement用于本地法规资料或已核验Web法规直接规定的内容，必须引用sourceRefs。
   - official_interpretation用于官方解释，必须引用来源。
   - standard_guidance用于原文属于标准或指南的内容，必须引用来源，不能写成法定义务。
   - engineering_recommendation可以是独立工程方案，允许无来源，但必须明确“建议、非强制”，不得使用“法规要求、法定必须”等措辞。
   - uncertainty只用于无法确认的业务内容。
6. serviceProviderActions只在职责、字段/API、流程或实施类问题中输出，最多3条，必须写明owner、priority、deliverable。概念和定义类问题应为空。只有条文直接规定服务商义务时使用direct_service_provider_duty；其他主体承担义务但服务商需要守住边界时使用legal_responsibility_boundary；工程行动使用derived_implementation_action。
7. unresolvedQuestions只放会影响方案选择、合规判断或上线决策的真实缺口，并写明impact和需要补充什么证据。不要放“某段被删除、部分支持、Web失败”等系统审核信息。
8. future来源必须明确“已公布但尚未生效”和生效日期。不得创造sourceId、法规义务、标准参数、接口契约或Web事实。
9. 对法规没给出的技术细节，不要反复只写“无法确认”。先说明法规边界，再给明确标注为engineering_recommendation的可配置实现方案、示例和等待后续规范时的调整点。
10. 不使用会话历史回答作为法规证据；历史只用于理解“他的、这个、继续”等指代。

回答时点：{(request.as_of_date or date.today()).isoformat()}
用户问题：{request.question}
会话上下文：{json.dumps(QAService._memory_payload(history), ensure_ascii=False)}
问题画像：{json.dumps(assessment.get('questionProfile', {}), ensure_ascii=False)}
研究评估：{json.dumps(assessment, ensure_ascii=False)}
Web校验未通过事项：{json.dumps(rejected_web, ensure_ascii=False)}
已验证来源：
{json.dumps([item.model_dump(mode='json') for item in sources], ensure_ascii=False)}
"""

    @staticmethod
    def _work_product_item_count(answer: WorkProductReport) -> int:
        return (
            len(answer.executiveAnswer)
            + len(answer.keyTakeaways)
            + sum(len(block.items) for block in answer.contentBlocks)
            + len(answer.serviceProviderActions)
        )

    def _work_product_quality_gaps(
        self, answer: WorkProductReport, profile: dict[str, Any]
    ) -> list[str]:
        gaps: list[str] = []
        if answer.status in {"answered", "partial"} and not answer.executiveAnswer:
            gaps.append("缺少直接回答用户问题的结论")
        if answer.status in {"answered", "partial"} and not any(
            block.blockType == "deep_analysis" and block.items
            for block in answer.contentBlocks
        ):
            gaps.append("缺少对法规依据、适用条件和限制的详细分析")
        return gaps

    @staticmethod
    def _expansion_prompt(
        request: QuestionRequest,
        answer: WorkProductReport,
        sources: list[ReportSource],
        assessment: dict[str, Any],
        quality_gaps: list[str],
    ) -> str:
        return f"""你是法规答复的高级编辑。首稿已经通过基础来源校验，但遗漏了直接结论或详细分析。请输出一份完整替代稿，不是只输出新增部分。

修订要求：
1. 保留首稿中正确、具体、有来源的内容，针对QUALITY_GAPS逐项补足。
2. 保持正文优先：1至3条直接结论、一个deep_analysis，最多再加一个有必要的表格或流程。不要为字数、模块数或形式扩写。
3. 法律要求、官方解释、本地标准和工程建议继续严格分层。工程建议必须明确为“建议、非强制”，但应给出字段、负责人、输入输出、验收条件或示例等实质内容。
4. unresolvedQuestions只放影响业务决策的真实问题，不得写内部审核过程。
5. 不得创造来源编号或法规事实。

用户问题：{request.question}
问题画像：{json.dumps(assessment.get('questionProfile', {}), ensure_ascii=False)}
QUALITY_GAPS：{json.dumps(quality_gaps, ensure_ascii=False)}
首稿：{json.dumps(answer.model_dump(mode='json', exclude={'sources', 'researchTrace', 'qualityNotes', 'timeContext'}), ensure_ascii=False)}
已验证来源：{json.dumps([item.model_dump(mode='json') for item in sources], ensure_ascii=False)}
"""

    @staticmethod
    def _validate_work_product(
        answer: WorkProductReport, profile: dict[str, Any] | None = None
    ) -> WorkProductReport:
        source_map = {source.sourceId: source for source in answer.sources if source.verified}
        authoritative = {
            source_id
            for source_id, source in source_map.items()
            if source.origin == "local" or source.authorityKind == "legal_act"
        }
        official = {
            source_id
            for source_id, source in source_map.items()
            if source.origin == "local" or source.origin == "web"
        }
        standard = {
            source_id
            for source_id, source in source_map.items()
            if source.origin == "local"
        }
        seen: set[str] = set()
        used: set[str] = set()
        quality = list(answer.qualityNotes)
        removed_material = False
        note_index = len(quality)

        def note(kind: str, message: str, related_id: str | None = None) -> None:
            nonlocal note_index
            note_index += 1
            quality.append(
                QualityNoteV4(
                    noteId=f"QN-{note_index:03d}",
                    type=kind,
                    message=message,
                    relatedId=related_id,
                )
            )

        def validate_item(item_id: str, basis: str, refs: list[str]) -> tuple[bool, list[str]]:
            nonlocal removed_material
            if not item_id or item_id in seen:
                note("removed", f"内容编号{item_id or '为空'}重复或无效，已移除", item_id or None)
                removed_material = True
                return False, []
            seen.add(item_id)
            valid_refs = list(dict.fromkeys(ref for ref in refs if ref in source_map))
            if len(valid_refs) != len(set(refs)):
                note("coverage", "部分来源编号不存在或重复，已清理", item_id)
            allowed = True
            if basis == "regulatory_requirement" and not authoritative.intersection(valid_refs):
                allowed = False
                note("removed", "标为法规要求但缺少本地法规资料或已核验Web法规来源，已移除", item_id)
            elif basis == "official_interpretation" and not official.intersection(valid_refs):
                allowed = False
                note("removed", "标为官方解释但缺少官方来源，已移除", item_id)
            elif basis == "standard_guidance" and not standard.intersection(valid_refs):
                allowed = False
                note("removed", "标为标准指导但缺少本地资料来源，已移除", item_id)
            if not allowed:
                removed_material = True
                return False, valid_refs
            used.update(valid_refs)
            return True, valid_refs

        executive = []
        for item in answer.executiveAnswer:
            valid, refs = validate_item(item.statementId, item.basis, item.sourceRefs)
            if valid:
                item.sourceRefs = refs
                executive.append(item)
        answer.executiveAnswer = executive

        takeaways = []
        for item in answer.keyTakeaways:
            valid, refs = validate_item(item.statementId, item.basis, item.sourceRefs)
            if valid:
                item.sourceRefs = refs
                takeaways.append(item)
        answer.keyTakeaways = takeaways

        blocks = []
        for block in answer.contentBlocks:
            accepted = []
            for item in block.items:
                valid, refs = validate_item(item.itemId, item.basis, item.sourceRefs)
                if not valid:
                    continue
                item.sourceRefs = refs
                if block.columns:
                    if len(item.cells) < len(block.columns):
                        item.cells.extend([""] * (len(block.columns) - len(item.cells)))
                        note("coverage", "表格单元格少于列数，已补空值", item.itemId)
                    elif len(item.cells) > len(block.columns):
                        item.cells = item.cells[: len(block.columns)]
                        note("coverage", "表格单元格多于列数，已截断", item.itemId)
                accepted.append(item)
            block.items = accepted
            if accepted:
                blocks.append(block)
        answer.contentBlocks = blocks

        actions = []
        for item in answer.serviceProviderActions:
            valid, refs = validate_item(item.actionId, item.basis, item.sourceRefs)
            if valid:
                item.sourceRefs = refs
                actions.append(item)
        answer.serviceProviderActions = actions

        if profile is not None:
            answer.executiveAnswer = answer.executiveAnswer[:3]
            executive_text = {_normalize(item.text) for item in answer.executiveAnswer}
            distinct_takeaways = []
            for item in answer.keyTakeaways:
                normalized_text = _normalize(item.text)
                if not normalized_text or normalized_text in executive_text:
                    continue
                executive_text.add(normalized_text)
                distinct_takeaways.append(item)
                if len(distinct_takeaways) == 3:
                    break
            answer.keyTakeaways = distinct_takeaways

            deep_analysis = next(
                (block for block in answer.contentBlocks if block.blockType == "deep_analysis"),
                None,
            )
            suggested = str(profile.get("suggestedBlockType", "none"))
            structured = next(
                (
                    block
                    for block in answer.contentBlocks
                    if block.blockType == suggested and block is not deep_analysis
                ),
                None,
            )
            selected_blocks = [block for block in (deep_analysis, structured) if block is not None]
            if not selected_blocks and answer.contentBlocks:
                selected_blocks = [answer.contentBlocks[0]]
            answer.contentBlocks = selected_blocks[:2]

            provider_intents = {
                "technical_design",
                "process",
                "responsibility",
                "implementation",
            }
            if provider_intents.intersection(profile.get("intents", [])):
                answer.serviceProviderActions = answer.serviceProviderActions[:3]
            else:
                answer.serviceProviderActions = []

        answer.qualityNotes = quality
        used = {
            ref
            for refs in (
                *[item.sourceRefs for item in answer.executiveAnswer],
                *[item.sourceRefs for item in answer.keyTakeaways],
                *[
                    item.sourceRefs
                    for block in answer.contentBlocks
                    for item in block.items
                ],
                *[item.sourceRefs for item in answer.serviceProviderActions],
            )
            for ref in refs
        }
        answer.sources = [source for source in answer.sources if source.sourceId in used]
        substantive = bool(answer.executiveAnswer or answer.contentBlocks)
        if not substantive:
            answer.status = "unresolved"
        elif answer.unresolvedQuestions or removed_material:
            answer.status = "partial"
        return answer

    @staticmethod
    def _work_product_verification_prompt(
        request: QuestionRequest, answer: WorkProductReport, profile: dict[str, Any]
    ) -> str:
        return f"""你是独立的法规准确性和落地价值审核员。只依据报告附带的已验证来源，不使用网络或模型记忆。

审核规则：
1. regulatory_requirement必须由引用来源直接支持，检查主体、动作、时点、范围和例外；不支持则列入unsupportedItemIds。
2. 所有本地文件具有相同证据资格；仍须根据引用原文判断内容属于法规要求、标准指导还是工程建议。
3. engineering_recommendation允许没有来源，不要因为它不是法规原文而删除；只检查是否明确为建议、是否与法规冲突、是否误用强制措辞。
4. 只有确实无法成立的内容才删除。可纠正的年份、主体、范围或措辞错误必须写入revisions并给出完整替代文本；部分支持内容优先修订或降级分类，避免把有价值的实施方案机械删掉，也不能让已发现的事实错误留在正文。
5. 检查直接结论和详细分析是否回答了用户明确提出的所有子问题。只有遗漏实质问题时才写入missingDeliverables；不得因字数短、模块少、没有表格、清单、示例或服务商行动而判定不合格。
6. 只输出简短审核结果，不输出隐藏思维过程。

用户问题：{request.question}
问题画像：{json.dumps(profile, ensure_ascii=False)}
法规答复及来源：{json.dumps(answer.model_dump(mode='json'), ensure_ascii=False)}
"""

    @staticmethod
    def _apply_work_product_verification(
        answer: WorkProductReport, verification: dict[str, Any]
    ) -> WorkProductReport:
        unsupported = {str(item) for item in verification.get("unsupportedItemIds", [])}
        partial = {str(item) for item in verification.get("partialItemIds", [])}
        reclassifications = {
            str(item.get("itemId")): item
            for item in verification.get("reclassifications", [])
            if isinstance(item, dict) and item.get("itemId")
        }
        revisions = {
            str(item.get("itemId")): item
            for item in verification.get("revisions", [])
            if isinstance(item, dict) and item.get("itemId") and item.get("revisedText")
        }
        note_index = len(answer.qualityNotes)

        def add_note(kind: str, message: str, related_id: str | None = None) -> None:
            nonlocal note_index
            note_index += 1
            answer.qualityNotes.append(
                QualityNoteV4(
                    noteId=f"QV-{note_index:03d}",
                    type=kind,
                    message=message,
                    relatedId=related_id,
                )
            )

        def apply(item: Any, item_id: str) -> bool:
            if item_id in unsupported:
                add_note("removed", "独立审核认定内容缺少支持，已移除", item_id)
                return False
            change = reclassifications.get(item_id)
            if change:
                item.basis = change.get("newBasis", item.basis)
                add_note("partial_support", f"依据性质已调整：{change.get('reason', '')}", item_id)
            revision = revisions.get(item_id)
            if revision:
                revised_text = str(revision.get("revisedText", "")).strip()
                if hasattr(item, "text"):
                    item.text = revised_text
                elif hasattr(item, "detail"):
                    item.detail = revised_text
                add_note("partial_support", f"独立审核已纠正文案：{revision.get('reason', '')}", item_id)
            if item_id in partial:
                add_note("partial_support", "独立审核认定该项仅获部分支持，已保留并标记", item_id)
            return True

        answer.executiveAnswer = [item for item in answer.executiveAnswer if apply(item, item.statementId)]
        answer.keyTakeaways = [item for item in answer.keyTakeaways if apply(item, item.statementId)]
        for block in answer.contentBlocks:
            block.items = [item for item in block.items if apply(item, item.itemId)]
        answer.contentBlocks = [block for block in answer.contentBlocks if block.items]
        answer.serviceProviderActions = [
            item for item in answer.serviceProviderActions if apply(item, item.actionId)
        ]
        for missing in verification.get("missingDeliverables", []):
            add_note("coverage", f"回答仍缺少交付物：{missing}")
        overall = verification.get("overall")
        if overall == "fail":
            answer.status = "unresolved"
        elif overall == "partial" and answer.status == "answered":
            answer.status = "partial"
        return answer

    @staticmethod
    def _report_paragraphs(answer: ResearchReport) -> list[ReportParagraph]:
        return [*answer.executiveSummary, *(p for section in answer.analysisSections for p in section.paragraphs)]

    @staticmethod
    def _validate_report(answer: ResearchReport) -> ResearchReport:
        source_map = {source.sourceId: source for source in answer.sources if source.verified}
        authoritative = {
            source_id
            for source_id, source in source_map.items()
            if source.origin == "local" or source.authorityKind == "legal_act"
        }
        used: set[str] = set()
        seen_paragraph_ids: set[str] = set()
        removed = False

        def validate_paragraph(paragraph: ReportParagraph) -> ReportParagraph | None:
            nonlocal removed
            if paragraph.paragraphId in seen_paragraph_ids:
                removed = True
                answer.uncertainties.append(f"报告包含重复段落编号{paragraph.paragraphId}，重复内容已阻断")
                return None
            seen_paragraph_ids.add(paragraph.paragraphId)
            paragraph.sourceRefs = list(dict.fromkeys(ref for ref in paragraph.sourceRefs if ref in source_map))
            if paragraph.nature in {"legal_analysis", "process_explanation"} and not paragraph.sourceRefs:
                removed = True
                answer.uncertainties.append(f"段落{paragraph.paragraphId}缺少可核验来源，已从报告移除")
                return None
            if paragraph.nature == "legal_analysis" and not authoritative.intersection(paragraph.sourceRefs):
                removed = True
                answer.uncertainties.append(f"段落{paragraph.paragraphId}缺少正式法规依据，已从确定性结论中移除")
                return None
            used.update(paragraph.sourceRefs)
            return paragraph

        answer.executiveSummary = [item for item in (validate_paragraph(p) for p in answer.executiveSummary) if item]
        for section in answer.analysisSections:
            section.paragraphs = [item for item in (validate_paragraph(p) for p in section.paragraphs) if item]
        answer.analysisSections = [section for section in answer.analysisSections if section.paragraphs]

        accepted_impacts = []
        for item in answer.serviceProviderImpact:
            item.sourceRefs = list(dict.fromkeys(ref for ref in item.sourceRefs if ref in source_map))
            if item.nature in {"direct_legal_duty", "direct_service_provider_duty", "legal_responsibility_boundary"} and not authoritative.intersection(item.sourceRefs):
                removed = True
                answer.uncertainties.append(f"“{item.title}”缺少服务商直接法定义务依据，已阻断")
                continue
            used.update(item.sourceRefs)
            accepted_impacts.append(item)
        answer.serviceProviderImpact = accepted_impacts
        answer.uncertainties = list(dict.fromkeys(item for item in answer.uncertainties if item.strip()))
        answer.sources = [source for source in answer.sources if source.sourceId in used]
        substantive = any(
            paragraph.nature in {"legal_analysis", "process_explanation"}
            for paragraph in QAService._report_paragraphs(answer)
        )
        if not substantive:
            answer.status = "unresolved"
        elif removed or (answer.uncertainties and answer.status == "answered"):
            answer.status = "partial"
        return answer

    @staticmethod
    def _report_verification_prompt(request: QuestionRequest, answer: ResearchReport) -> str:
        return f"""你是独立法规报告审核员。只根据报告中附带的已验证来源核查，不使用网络或模型记忆。

逐段检查：
- sourceRefs是否真正支持整段内容，是否存在主体错置、时间效力错误、过度推断或把官方说明当成法规。
- 是否完整回答用户问题的所有重要组成部分。
- 是否把经济运营商义务错误表述为服务商直接法定义务。direct_service_provider_duty必须有明确以服务商为义务主体的条文；其他主体承担的义务必须分类为legal_responsibility_boundary，否则列入unsupportedServiceItemIds。
- implementation_advice不作为法律事实审核，但若伪装成法定义务应判不支持。
unsupportedParagraphIds列出应删除段落；partialParagraphIds列出仅获部分支持段落；unsupportedServiceItemIds列出主体错置或证据不支持的服务商条目；coverageGaps只写影响答案完整性的缺口。
只输出简短审核结论，不输出隐藏思维过程。

用户问题：{request.question}
报告及来源：
{json.dumps(answer.model_dump(mode='json'), ensure_ascii=False)}
"""

    @staticmethod
    def _apply_report_verification(answer: ResearchReport, verification: dict[str, Any]) -> ResearchReport:
        unsupported = {str(item) for item in verification.get("unsupportedParagraphIds", [])}
        partial = {str(item) for item in verification.get("partialParagraphIds", [])}
        unsupported_service = {str(item) for item in verification.get("unsupportedServiceItemIds", [])}
        answer.executiveSummary = [p for p in answer.executiveSummary if p.paragraphId not in unsupported]
        for section in answer.analysisSections:
            section.paragraphs = [p for p in section.paragraphs if p.paragraphId not in unsupported]
        answer.analysisSections = [section for section in answer.analysisSections if section.paragraphs]
        answer.serviceProviderImpact = [
            item for item in answer.serviceProviderImpact if item.itemId not in unsupported_service
        ]
        for paragraph_id in sorted(unsupported):
            answer.uncertainties.append(f"独立审核移除了证据不支持的段落{paragraph_id}")
        for paragraph_id in sorted(partial):
            answer.uncertainties.append(f"段落{paragraph_id}仅获得部分证据支持")
        for item_id in sorted(unsupported_service):
            answer.uncertainties.append(f"独立审核移除了证据不支持的服务商条目{item_id}")
        for gap in verification.get("coverageGaps", []):
            answer.uncertainties.append(f"回答覆盖仍不完整：{gap}")
        answer.uncertainties = list(dict.fromkeys(answer.uncertainties))
        if verification.get("overall") == "fail":
            answer.status = "unresolved"
        elif verification.get("overall") != "pass" or verification.get("coverageStatus") == "incomplete":
            answer.status = "partial"
        return answer

    @staticmethod
    def _event(stage: str, message: str) -> dict[str, Any]:
        return {
            "stage": stage,
            "message": message,
            "at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _generation_prompt(request: QuestionRequest, evidence: list, history: list[dict] | None = None) -> str:
        evidence_json = json.dumps([item.model_dump(mode="json") for item in evidence], ensure_ascii=False)
        memory_json = json.dumps(QAService._memory_payload(history or []), ensure_ascii=False)
        as_of = request.as_of_date.isoformat() if request.as_of_date else "当前有效版本"
        return f"""你是服务商合规团队的专业法规问答助手。严格只使用<EVIDENCE>中的法规片段回答。

硬性规则：
1. 把回答拆成可独立核验的原子论点；每个legal_fact必须引用至少一个证据ID。
2. 不得创造证据ID、文件名、Article、页码或原文。quote必须逐字复制证据片段中的连续文字。
3. 所有本地法规资料按相同证据资格处理；法律结论必须由引用原文直接支持。
4. 法规未直接规定时写入missingEvidence或标为uncertain。
5. 实施建议只能放implementationAdvice，性质必须是implementation_advice，不能冒充法定义务。
6. 结论与解释使用中文，quote保留源文件原文。
7. 不使用网络、常识或模型记忆补齐法规。
8. summary必须先直接回答用户问题，不能只写“证据未定义”；有明确能力但缺少技术契约时，要同时写明“法规明确了什么”和“法规没有明确什么”。
9. 对“有哪些、包括什么、流程是什么”类问题，必须覆盖证据中所有直接相关条款，并按类别完整列举，不得仅选择一两个示例。
10. 每个claim的text写论点，analysis写法规原文如何支持该论点、适用主体、边界及与用户问题的关系；不得重复一句话敷衍。
11. 必须区分：法规明确称为API的接口、通过API完成的业务动作、并未被法规定义为API的系统能力、法规尚未提供的端点/方法/字段/鉴权等技术契约。
12. 证据的applicabilityStatus为future时，必须明确写“已公布但尚未生效”及effectiveDate，不得表述为当前已生效义务。
13. 用户主要身份是数字产品护照服务商。法规直接规定服务商职责时写成legal_fact；仅能从经济运营商流程推导的系统建设动作放入implementationAdvice，并明确非直接法定义务。
14. 如果直接回答来自future证据，同时存在current证据规定了现行基础框架，summary和claims必须分别交代“现行规则已经明确什么”与“待生效规则将新增什么”，不能让用户误以为当前完全没有规则。
15. <CONVERSATION_MEMORY>只用于理解“这个、上一个、那服务商呢”等指代、用户身份和延续主题；历史回答不是法规证据，不得引用、复述为法律事实或替代当前<EVIDENCE>。
16. 当前问题的所有法律结论都必须由本轮<EVIDENCE>重新支持。如果历史结论在本轮证据中找不到支持，必须忽略或列为不确定事项。
17. 用户比较两个或多个相似法规术语时，逐项说明：是否为法规正式定义术语、识别对象、赋予或生成主体、产生时点、法律功能、相互关系，以及法规未规定的技术字段。不得因中文业务俗称相近而合并概念。
18. 对“电池唯一标识符/电池护照唯一标识符”必须区分：Battery Regulation的unique identifier、并非独立法定术语的“battery passport unique identifier”叫法、注册中心生成的unique registration identifier；服务商内部字段建议只能放implementationAdvice。

提问日期：{as_of}
时间口径：{'当前有效及已公布待生效法规' if request.include_future_rules else '仅提问日期当日有效法规'}
用户问题：{request.question}

<CONVERSATION_MEMORY>
{memory_json}
</CONVERSATION_MEMORY>

<EVIDENCE>
{evidence_json}
</EVIDENCE>
"""

    @staticmethod
    def _retrieval_context_queries(history: list[dict]) -> list[str]:
        return [
            str(item.get("question", "")).strip()[:400]
            for item in history[-2:]
            if str(item.get("question", "")).strip()
        ]

    @staticmethod
    def _memory_payload(history: list[dict]) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for item in history[-5:]:
            answer = item.get("answer") if isinstance(item.get("answer"), dict) else {}
            claims = answer.get("claims") if isinstance(answer.get("claims"), list) else []
            report_summary = answer.get("executiveSummary") if isinstance(answer.get("executiveSummary"), list) else []
            work_summary = answer.get("executiveAnswer") if isinstance(answer.get("executiveAnswer"), list) else []
            work_takeaways = answer.get("keyTakeaways") if isinstance(answer.get("keyTakeaways"), list) else []
            summary = str(answer.get("summary", ""))
            if not summary and report_summary:
                summary = "\n".join(
                    str(paragraph.get("text", ""))
                    for paragraph in report_summary[:4]
                    if isinstance(paragraph, dict) and paragraph.get("text")
                )
            if not summary and work_summary:
                summary = "\n".join(
                    str(statement.get("text", ""))
                    for statement in work_summary[:4]
                    if isinstance(statement, dict) and statement.get("text")
                )
            payload.append(
                {
                    "question": str(item.get("question", ""))[:600],
                    "answerSummary": summary[:1200],
                    "claimHeadlines": [
                        str(claim.get("text", ""))[:300]
                        for claim in claims[:6]
                        if isinstance(claim, dict) and claim.get("text")
                    ] or [
                        str(item.get("text", ""))[:300]
                        for item in work_takeaways[:6]
                        if isinstance(item, dict) and item.get("text")
                    ],
                }
            )
        return payload

    @staticmethod
    def _verification_prompt(answer: LegalAnswer, evidence: list) -> str:
        return f"""你是独立法规证据审核员和答案完整性复核员。逐项检查论点，并检查答案是否遗漏证据中直接回答用户问题的重要内容。

审核规则：
- 只依据给定证据，不使用外部知识。
- 证据仅部分覆盖论点时判partially_supported。
- 推断超出条文、主体错置、时间或适用范围错误时判unsupported或conflict。
- 不评价implementationAdvice，只审核claims。
- revisedSummary应保留正确内容，并纠正不完整、主体错置或未标注未来生效的问题。
- 对证据中存在、但初稿遗漏的重要结论，写入supplementalClaims；引用规则与初稿完全相同。
- 特别检查是否混淆“API”“通过API完成的动作”“普通平台能力”和“法规未定义的技术契约”。
- 比较相似标识符时，检查是否分别交代正式术语、识别对象、产生主体、法律功能和非正式叫法；不得把电池unique identifier、所谓battery passport unique identifier和registry unique registration identifier混为一谈。
- future证据只能支持已公布待生效规则，不能表述为当前生效义务。
- 没有遗漏时coverageStatus为complete、supplementalClaims和coverageGaps为空。

<ANSWER>
{json.dumps(answer.model_dump(mode='json'), ensure_ascii=False)}
</ANSWER>
<EVIDENCE>
{json.dumps([item.model_dump(mode='json') for item in evidence], ensure_ascii=False)}
</EVIDENCE>
"""

    @staticmethod
    def _deterministic_validate(answer: LegalAnswer, evidence: list) -> LegalAnswer:
        evidence_map = {item.evidenceId: item for item in evidence}
        valid_claims = []
        missing = list(answer.missingEvidence)
        for claim in answer.claims:
            valid_citations = []
            for citation in claim.citations:
                source = evidence_map.get(citation.evidenceId)
                if not source:
                    continue
                quote = _normalize(citation.quote)
                if not quote or quote not in _normalize(source.quote):
                    continue
                if citation.documentId != source.documentId or citation.page != source.page:
                    continue
                citation.article = source.article
                citation.documentTitle = source.documentTitle
                citation.filename = source.filename
                citation.publicationDate = source.publicationDate
                citation.effectiveDate = source.effectiveDate
                citation.expiryDate = source.expiryDate
                citation.applicabilityStatus = source.applicabilityStatus
                valid_citations.append(citation)
            claim.citations = valid_citations
            if claim.nature == "legal_fact":
                if not valid_citations:
                    missing.append(f"论点{claim.claimId}缺少直接证据：{claim.text}")
                    continue
            if not valid_citations and claim.nature != "uncertain":
                missing.append(f"论点{claim.claimId}引用未通过原文校验")
                continue
            valid_claims.append(claim)
        answer.claims = valid_claims
        answer.searchedSources = sorted({item.documentTitle for item in evidence})
        answer.missingEvidence = list(dict.fromkeys(missing))
        if not valid_claims:
            answer.status = "insufficient_evidence"
        elif answer.missingEvidence:
            answer.status = "partial"
        return answer

    @staticmethod
    def _apply_verification(answer: LegalAnswer, verification: dict[str, Any]) -> LegalAnswer:
        verdicts = {
            item.get("claimId"): item
            for item in verification.get("results", [])
            if isinstance(item, dict) and item.get("claimId")
        }
        accepted = []
        for claim in answer.claims:
            result = verdicts.get(claim.claimId)
            if not result:
                answer.missingEvidence.append(f"论点{claim.claimId}未获得独立审核结果")
                continue
            verdict = result.get("verdict")
            if verdict == "supported":
                claim.supportStatus = "supported"
                accepted.append(claim)
            elif verdict == "partially_supported":
                claim.supportStatus = "partially_supported"
                accepted.append(claim)
                answer.missingEvidence.append(f"论点{claim.claimId}仅获部分支持：{result.get('reason', '')}")
            else:
                answer.missingEvidence.append(f"论点{claim.claimId}被审核阻断：{result.get('reason', '')}")
        answer.claims = accepted
        existing_ids = {claim.claimId for claim in accepted}
        for payload in verification.get("supplementalClaims", []):
            try:
                claim = Claim.model_validate(payload)
            except ValidationError:
                answer.missingEvidence.append("完整性复核生成的补充论点未通过Schema校验")
                continue
            if claim.claimId in existing_ids:
                answer.missingEvidence.append(f"完整性复核返回重复论点编号：{claim.claimId}")
                continue
            existing_ids.add(claim.claimId)
            accepted.append(claim)
        revised_summary = str(verification.get("revisedSummary", "")).strip()
        if revised_summary:
            answer.summary = revised_summary
        if verification.get("coverageStatus") == "incomplete" and not verification.get("supplementalClaims"):
            for gap in verification.get("coverageGaps", []):
                answer.missingEvidence.append(f"回答覆盖仍不完整：{gap}")
        answer.missingEvidence = list(dict.fromkeys(answer.missingEvidence))
        if not accepted:
            answer.status = "insufficient_evidence"
        elif answer.missingEvidence or verification.get("overall") != "pass":
            answer.status = "partial"
        return answer

    @staticmethod
    def _time_context(request: QuestionRequest, evidence: list) -> TimeContext:
        as_of = request.as_of_date or date.today()
        notices: list[TimeNotice] = []
        seen: set[str] = set()
        for item in evidence:
            if item.documentId in seen or item.applicabilityStatus != "future":
                continue
            seen.add(item.documentId)
            effective = item.effectiveDate.isoformat() if item.effectiveDate else "尚未明确"
            notices.append(
                TimeNotice(
                    documentId=item.documentId,
                    documentTitle=item.documentTitle,
                    applicabilityStatus="future",
                    effectiveDate=item.effectiveDate,
                    note=f"该正式文件已公布，但截至{as_of.isoformat()}尚未生效；生效日期为{effective}。",
                )
            )
        return TimeContext(
            asOfDate=as_of,
            includesFutureRules=request.include_future_rules,
            notices=notices,
        )
