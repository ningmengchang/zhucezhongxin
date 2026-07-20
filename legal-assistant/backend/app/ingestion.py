from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

import fitz

from .database import Database, utc_now
from .models import DocumentCreate


ARTICLE_RE = re.compile(r"^\s*(Article\s+\d+[a-zA-Z]?|ANNEX\s+[IVXLCDM]+)\s*$", re.IGNORECASE)
PARAGRAPH_RE = re.compile(r"^\s*(\d+\.|\([a-z0-9ivx]+\))\s+")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _chunk_blocks(blocks: Iterable[str], max_chars: int = 2200) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for raw in blocks:
        block = normalize_text(raw)
        if not block:
            continue
        if current and current_size + len(block) + 1 > max_chars:
            chunks.append("\n".join(current))
            current = current[-1:]
            current_size = sum(len(item) for item in current)
        current.append(block)
        current_size += len(block) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def extract_pdf(path: Path) -> list[dict]:
    output: list[dict] = []
    current_article: str | None = None
    position = 0
    with fitz.open(path) as pdf:
        for page_number, page in enumerate(pdf, start=1):
            text = page.get_text("text")
            blocks = [block[4] for block in page.get_text("blocks") if len(block) > 4]
            if not blocks and normalize_text(text):
                blocks = [text]
            for block in blocks:
                segments: list[tuple[str | None, str]] = []
                segment_lines: list[str] = []
                for line in block.splitlines():
                    match = ARTICLE_RE.match(line)
                    if match:
                        if segment_lines:
                            segments.append((current_article, "\n".join(segment_lines)))
                            segment_lines = []
                        current_article = normalize_text(match.group(1))
                    segment_lines.append(line)
                if segment_lines:
                    segments.append((current_article, "\n".join(segment_lines)))
                for article, segment in segments:
                    for chunk in _chunk_blocks([segment]):
                        paragraph_match = PARAGRAPH_RE.match(chunk)
                        output.append(
                            {
                                "article": article,
                                "paragraph": paragraph_match.group(1) if paragraph_match else None,
                                "page": page_number,
                                "text": chunk,
                                "position": position,
                            }
                        )
                        position += 1
    return output


def _tessdata_dir() -> Path | None:
    candidates = []
    configured = os.getenv("TESSDATA_PREFIX")
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            Path("/usr/share/tesseract-ocr/5/tessdata"),
            Path("/usr/share/tesseract-ocr/4.00/tessdata"),
            Path("/usr/share/tesseract-ocr/tessdata"),
        ]
    )
    return next((path for path in candidates if (path / "eng.traineddata").is_file()), None)


def extract_pdf_ocr(path: Path) -> list[dict]:
    tesseract = shutil.which("tesseract")
    tessdata = _tessdata_dir()
    if not tesseract or not tessdata:
        return []
    languages = "eng+chi_sim" if (tessdata / "chi_sim.traineddata").is_file() else "eng"
    output: list[dict] = []
    current_article: str | None = None
    position = 0
    with fitz.open(path) as pdf:
        for page_number, page in enumerate(pdf, start=1):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), colorspace=fitz.csGRAY, alpha=False)
            try:
                process = subprocess.run(
                    [
                        tesseract,
                        "stdin",
                        "stdout",
                        "-l",
                        languages,
                        "--tessdata-dir",
                        str(tessdata),
                    ],
                    input=pixmap.tobytes("png"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=90,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                continue
            if process.returncode != 0:
                continue
            text = process.stdout.decode("utf-8", errors="replace")
            for chunk in _chunk_blocks(re.split(r"\n\s*\n", text)):
                for line in chunk.splitlines():
                    article_match = ARTICLE_RE.match(line)
                    if article_match:
                        current_article = normalize_text(article_match.group(1))
                        break
                paragraph_match = PARAGRAPH_RE.match(chunk)
                output.append(
                    {
                        "article": current_article,
                        "paragraph": paragraph_match.group(1) if paragraph_match else None,
                        "page": page_number,
                        "text": chunk,
                        "position": position,
                    }
                )
                position += 1
    return output


def extract_text(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    chunks = _chunk_blocks(re.split(r"\n\s*\n", text))
    return [
        {"article": None, "paragraph": None, "page": 1, "text": chunk, "position": i}
        for i, chunk in enumerate(chunks)
    ]


def ingest_document(database: Database, spec: DocumentCreate, base_dir: Path, *, replace: bool = False) -> dict:
    path = Path(spec.path)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"法规文件不存在: {path}")
    if path.suffix.lower() not in {".pdf", ".md", ".txt"}:
        raise ValueError("试点版仅支持 PDF、Markdown 和纯文本")

    digest = sha256_file(path)
    existing = database.get_document_by_hash(digest)
    if existing:
        if not replace:
            return existing
        database.delete_document_by_hash(digest)

    raw_passages = extract_pdf(path) if path.suffix.lower() == ".pdf" else extract_text(path)
    if path.suffix.lower() == ".pdf" and not raw_passages:
        raw_passages = extract_pdf_ocr(path)
    if not raw_passages:
        raise ValueError("未提取到可检索文本；扫描件需要先启用OCR处理")

    document_id = f"DOC-{digest[:12].upper()}"
    now = utc_now()
    document = {
        "id": document_id,
        "title": spec.title or path.stem,
        "filename": path.name,
        "path": str(path),
        "sha256": digest,
        "source_level": spec.source_level.value,
        "review_status": spec.review_status.value,
        "jurisdiction": spec.jurisdiction,
        "authority": spec.authority,
        "official_url": spec.official_url,
        "publication_date": spec.publication_date.isoformat() if spec.publication_date else None,
        "effective_date": spec.effective_date.isoformat() if spec.effective_date else None,
        "expiry_date": spec.expiry_date.isoformat() if spec.expiry_date else None,
        "version_of": spec.version_of,
        "review_note": None,
        "created_at": now,
        "updated_at": now,
    }
    passages: list[dict] = []
    for item in raw_passages:
        raw_key = f"{document_id}:{item['page']}:{item['position']}:{item['text'][:120]}"
        evidence_id = f"EV-{hashlib.sha1(raw_key.encode()).hexdigest()[:12].upper()}"
        passages.append(
            {
                "id": evidence_id,
                "document_id": document_id,
                "article": item["article"],
                "paragraph": item["paragraph"],
                "page": item["page"],
                "text": item["text"],
                "normalized_text": normalize_text(item["text"]).lower(),
                "position": item["position"],
            }
        )
    database.insert_document(document, passages)
    document["passage_count"] = len(passages)
    return document
