from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

import httpx


OFFICIAL_HOSTS = {
    "eur-lex.europa.eu",
    "data.europa.eu",
    "ec.europa.eu",
    "commission.europa.eu",
    "op.europa.eu",
}
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


def _official_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in OFFICIAL_HOSTS)


async def archive_official_pdf(url: str, output_dir: Path) -> Path:
    if not url.startswith("https://") or not _official_host(url):
        raise ValueError("只允许HTTPS官方白名单域名")
    output_dir.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(follow_redirects=True, timeout=45) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            if not _official_host(str(response.url)):
                raise ValueError("官方链接重定向到了非白名单域名")
            content_type = response.headers.get("content-type", "").lower()
            if "pdf" not in content_type and not str(response.url).lower().endswith(".pdf"):
                raise ValueError("试点版官方归档仅接受PDF源文件")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_DOWNLOAD_BYTES:
                    raise ValueError("官方文件超过50MB限制")
    digest = hashlib.sha256(data).hexdigest()
    path = output_dir / f"{digest}.pdf"
    if not path.exists():
        path.write_bytes(data)
    return path

