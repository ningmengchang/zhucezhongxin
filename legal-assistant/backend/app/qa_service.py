from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any

from pydantic import ValidationError

from .codex_runner import CodexError, CodexRunner
from .database import Database
from .models import Claim, JobStatus, LegalAnswer, QuestionRequest, TimeContext, TimeNotice
from .retrieval import Retriever


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


class QAService:
    def __init__(self, database: Database, retriever: Retriever, runner: CodexRunner, queue_limit: int):
        self.database = database
        self.retriever = retriever
        self.runner = runner
        self.queue: asyncio.Queue[tuple[str, QuestionRequest]] = asyncio.Queue(maxsize=queue_limit)
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
        history = self.database.conversation_history(
            request.conversation_id,
            exclude_job_id=job_id,
            limit=self.runner.settings.conversation_memory_turns,
        )
        self.database.update_job(
            job_id,
            status=JobStatus.retrieving.value,
            event=self._event(
                "retrieving",
                f"正在检索已审核法规库；已载入{len(history)}轮会话记忆"
                if history
                else "正在检索已审核法规库",
            ),
        )
        context_queries = self._retrieval_context_queries(history)
        evidence = self.retriever.search(
            request.question,
            as_of_date=request.as_of_date,
            include_future_rules=request.include_future_rules,
            context_queries=context_queries,
            limit=self.runner.settings.max_evidence,
        )
        authoritative = [item for item in evidence if item.sourceLevel.value in {"A1", "A2"}]
        if not authoritative:
            answer = LegalAnswer(
                status="insufficient_evidence",
                summary="现有已审核法规库中没有足以支撑确定性结论的A类证据。",
                timeContext=self._time_context(request, evidence),
                missingEvidence=["需要补充或审核与该问题直接相关的正式法规源文件"],
                searchedSources=sorted({item.documentTitle for item in evidence}),
            )
            self.database.update_job(
                job_id,
                status=JobStatus.completed.value,
                answer=answer.model_dump(mode="json"),
                event=self._event("completed", "证据不足，已阻断确定性结论"),
            )
            return

        self.database.update_job(
            job_id,
            status=JobStatus.generating.value,
            event=self._event("generating", f"已选取{len(evidence)}条证据，正在生成论点"),
        )
        generation_prompt = self._generation_prompt(request, evidence, history)
        generation = await self.runner.run(generation_prompt, "legal_answer.schema.json")
        try:
            answer = LegalAnswer.model_validate(generation.payload)
        except ValidationError as exc:
            raise CodexError(f"Codex答案未通过应用层Schema校验: {exc}") from exc

        answer = self._deterministic_validate(answer, evidence)
        if self.runner.settings.codex_verify_enabled and answer.claims:
            self.database.update_job(
                job_id,
                status=JobStatus.verifying.value,
                event=self._event("verifying", "正在独立核验论点与法规证据"),
            )
            verification_prompt = self._verification_prompt(answer, evidence)
            verification = await self.runner.run(verification_prompt, "legal_verification.schema.json")
            answer = self._apply_verification(answer, verification.payload)
            answer = self._deterministic_validate(answer, evidence)

        answer.timeContext = self._time_context(request, evidence)

        self.database.update_job(
            job_id,
            status=JobStatus.completed.value,
            answer=answer.model_dump(mode="json"),
            event=self._event("completed", "答案及引用校验完成"),
        )

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
3. A1/A2可支持确定性法律结论；B/C只能补充；D不得支持确定性结论。
4. 法规未直接规定时写入missingEvidence或标为uncertain。
5. 实施建议只能放implementationAdvice，性质必须是implementation_advice，不能冒充法定义务。
6. 结论与解释使用中文，quote保留源文件原文。
7. 不使用网络、常识或模型记忆补齐法规。
8. summary必须先直接回答用户问题，不能只写“证据未定义”；有明确能力但缺少技术契约时，要同时写明“法规明确了什么”和“法规没有明确什么”。
9. 对“有哪些、包括什么、流程是什么”类问题，必须覆盖证据中所有直接相关的A1/A2条款，并按类别完整列举，不得仅选择一两个示例。
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
            payload.append(
                {
                    "question": str(item.get("question", ""))[:600],
                    "answerSummary": str(answer.get("summary", ""))[:1200],
                    "claimHeadlines": [
                        str(claim.get("text", ""))[:300]
                        for claim in claims[:6]
                        if isinstance(claim, dict) and claim.get("text")
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
- 对证据中存在、但初稿遗漏的重要A1/A2结论，写入supplementalClaims；引用规则与初稿完全相同。
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
                citation.sourceLevel = source.sourceLevel
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
                has_primary = any(item.sourceLevel.value in {"A1", "A2"} for item in valid_citations)
                if not has_primary:
                    missing.append(f"论点{claim.claimId}缺少A类直接证据：{claim.text}")
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
