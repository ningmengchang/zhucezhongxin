from __future__ import annotations

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


def _official_host(host: str, allowed_domains: tuple[str, ...]) -> bool:
    host = host.rstrip(".").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)


def _public_addresses(host: str) -> bool:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror:
        return False
    if not addresses:
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            return False
    return True


def _page_text(content: bytes, content_type: str) -> str:
    if "pdf" in content_type.lower() or content.startswith(b"%PDF"):
        document = fitz.open(stream=content, filetype="pdf")
        return "\n".join(page.get_text("text") for page in document)
    decoded = content.decode("utf-8", errors="replace")
    decoded = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", decoded)
    return html.unescape(re.sub(r"(?s)<[^>]+>", " ", decoded))


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class WebEvidenceValidator:
    def __init__(self, allowed_domains: tuple[str, ...], max_sources: int = 5):
        self.allowed_domains = allowed_domains
        self.max_sources = max_sources

    async def validate_many(self, payloads: list[dict[str, Any]]) -> tuple[list[ReportSource], list[str]]:
        accepted: list[ReportSource] = []
        rejected: list[str] = []
        for index, payload in enumerate(payloads[: self.max_sources], start=1):
            source, reason = await self._validate(payload, index)
            if source:
                accepted.append(source)
            else:
                rejected.append(reason)
        return accepted, rejected

    async def _validate(self, payload: dict[str, Any], index: int) -> tuple[ReportSource | None, str]:
        raw_url = str(payload.get("url", "")).strip()
        quote = str(payload.get("quote", "")).strip()
        parsed = urlparse(raw_url)
        host = (parsed.hostname or "").lower()
        label = str(payload.get("title") or raw_url or f"Web来源{index}")
        if parsed.scheme != "https" or not host or not _official_host(host, self.allowed_domains):
            return None, f"{label}：URL不属于允许的欧盟官方域名"
        if len(_normalized(quote)) < 20:
            return None, f"{label}：未返回可核验的法规原文"
        if not _public_addresses(host):
            return None, f"{label}：域名解析结果不是公共网络地址"

        try:
            final_url, body, content_type = await self._fetch(raw_url)
            page_text = _page_text(body, content_type)
        except Exception as exc:
            return None, f"{label}：官方页面读取失败（{str(exc)[:120]}）"
        if _normalized(quote) not in _normalized(page_text):
            return None, f"{label}：返回原文无法在官方页面中逐字核对"

        publication = _parse_date(payload.get("publicationDate"))
        effective = _parse_date(payload.get("effectiveDate"))
        today = date.today()
        applicability = "future" if effective and effective > today else "current" if effective else "unknown"
        return (
            ReportSource(
                sourceId=f"WEB-{index:02d}",
                origin="web",
                authorityKind=payload.get("authorityKind", "official_guidance"),
                title=label,
                quote=quote,
                url=final_url,
                publisher=str(payload.get("publisher", "")).strip() or host,
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
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, headers={"User-Agent": "LegalAssistant/0.4"}) as client:
            for _ in range(4):
                parsed = urlparse(current)
                host = (parsed.hostname or "").lower()
                if parsed.scheme != "https" or not _official_host(host, self.allowed_domains):
                    raise ValueError("重定向目标不在官方域名白名单")
                response = await client.get(current)
                if response.status_code in {301, 302, 303, 307, 308}:
                    target = response.headers.get("location")
                    if not target:
                        raise ValueError("官方页面返回空重定向")
                    current = urljoin(current, target)
                    continue
                response.raise_for_status()
                body = response.content
                if len(body) > MAX_WEB_BYTES:
                    raise ValueError("官方页面超过10MB验证上限")
                return str(response.url), body, response.headers.get("content-type", "")
        raise ValueError("官方页面重定向次数过多")
