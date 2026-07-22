from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import re
import socket
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import fitz
import httpx

from .models import ReportSource


MAX_WEB_BYTES = 10 * 1024 * 1024


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _public_addresses(host: str) -> bool:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror:
        return False
    return bool(addresses) and all(ipaddress.ip_address(address).is_global for address in addresses)


def _page_text(content: bytes, content_type: str) -> str:
    if "pdf" in content_type.lower() or content.startswith(b"%PDF"):
        document = fitz.open(stream=content, filetype="pdf")
        try:
            return "\n".join(page.get_text("text") for page in document)
        finally:
            document.close()
    decoded = content.decode("utf-8", errors="replace")
    decoded = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", decoded)
    return html.unescape(re.sub(r"(?s)<[^>]+>", " ", decoded))


class WebEvidenceValidator:
    def __init__(self, max_sources: int = 5):
        self.max_sources = max_sources

    async def validate_many(self, payloads: list[dict[str, Any]]) -> tuple[list[ReportSource], list[str]]:
        results = await asyncio.gather(
            *(self._validate(payload, index) for index, payload in enumerate(payloads[: self.max_sources], 1)),
            return_exceptions=True,
        )
        accepted: list[ReportSource] = []
        rejected: list[str] = []
        for result in results:
            if isinstance(result, Exception):
                rejected.append(f"外部来源验证失败：{str(result)[:160]}")
            elif result[0] is not None:
                accepted.append(result[0])
            else:
                rejected.append(result[1])
        return accepted, rejected

    async def _validate(self, payload: dict[str, Any], index: int) -> tuple[ReportSource | None, str]:
        raw_url = str(payload.get("url", "")).strip()
        quote = str(payload.get("quote", "")).strip()
        label = str(payload.get("title") or raw_url or f"外部来源{index}")
        parsed = urlparse(raw_url)
        host = (parsed.hostname or "").rstrip(".").lower()
        if parsed.scheme != "https" or not host:
            return None, f"{label}：仅接受有效HTTPS地址"
        if len(_normalized(quote)) < 20:
            return None, f"{label}：没有足够长的可核验原文"
        if not _public_addresses(host):
            return None, f"{label}：地址不是公共网络来源"

        try:
            final_url, body, content_type = await self._fetch(raw_url)
            page_text = _page_text(body, content_type)
        except Exception as exc:
            return None, f"{label}：页面读取失败（{str(exc)[:120]}）"
        if _normalized(quote) not in _normalized(page_text):
            return None, f"{label}：引文无法在页面中逐字核对"

        publication = payload.get("publicationDate")
        effective = payload.get("effectiveDate")
        if isinstance(publication, str):
            publication = date.fromisoformat(publication[:10])
        if isinstance(effective, str):
            effective = date.fromisoformat(effective[:10])
        applicability = "future" if effective and effective > date.today() else "current" if effective else "unknown"
        source_hash = hashlib.sha256(final_url.encode("utf-8")).hexdigest()[:12].upper()
        return (
            ReportSource(
                sourceId=f"WEB-{source_hash}",
                origin="web",
                authorityKind="official_guidance",
                title=label,
                quote=quote,
                url=final_url,
                publisher=str(payload.get("publisher") or host).strip(),
                publicationDate=publication,
                effectiveDate=effective,
                applicabilityStatus=applicability,
                accessedAt=datetime.now(timezone.utc),
                verified=True,
            ),
            "",
        )

    async def _fetch(self, url: str) -> tuple[str, bytes, str]:
        current = url
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=False,
            headers={"User-Agent": "LegalAssistant/0.8"},
        ) as client:
            for _ in range(4):
                parsed = urlparse(current)
                host = (parsed.hostname or "").rstrip(".").lower()
                if parsed.scheme != "https" or not host or not _public_addresses(host):
                    raise ValueError("重定向目标不是公共HTTPS地址")
                response = await client.get(current)
                if response.status_code in {301, 302, 303, 307, 308}:
                    target = response.headers.get("location")
                    if not target:
                        raise ValueError("页面返回空重定向")
                    current = urljoin(current, target)
                    continue
                response.raise_for_status()
                body = response.content
                if len(body) > MAX_WEB_BYTES:
                    raise ValueError("页面超过10MB验证上限")
                return str(response.url), body, response.headers.get("content-type", "")
        raise ValueError("页面重定向次数过多")
