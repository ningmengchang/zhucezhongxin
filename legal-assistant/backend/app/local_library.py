from __future__ import annotations

from pathlib import Path

from .config import settings
from .database import Database
from .ingestion import ingest_document, sha256_file
from .models import DocumentCreate
from .seed import SEED_DOCUMENTS


SUPPORTED_SUFFIXES = {".pdf", ".md", ".txt"}
EXCLUDED_DIRECTORIES = {"legal-assistant", ".git", ".pytest_cache", ".marscode", "__pycache__"}
EXCLUDED_FILENAMES = {"readme.md"}


def discover_local_files(source_dir: Path) -> list[Path]:
    root = source_dir.resolve()
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRECTORIES or part.startswith(".") for part in relative.parts[:-1]):
            continue
        if path.name.lower() in EXCLUDED_FILENAMES:
            continue
        files.append(path.resolve())
    return sorted(files, key=lambda item: str(item).lower())


def _curated_specs(source_dir: Path) -> dict[Path, DocumentCreate]:
    output: dict[Path, DocumentCreate] = {}
    for spec in SEED_DOCUMENTS:
        path = Path(spec.path)
        resolved = path.resolve() if path.is_absolute() else (source_dir / path).resolve()
        output[resolved] = spec
    return output


def sync_local_library(database: Database | None = None, source_dir: Path | None = None) -> dict:
    db = database or Database(settings.database_path)
    db.init()
    source_dir = (source_dir or settings.source_dir).resolve()
    curated = _curated_specs(source_dir)
    existing_documents = db.list_documents()
    existing_hashes = {item["sha256"] for item in existing_documents}
    imported = 0
    unchanged = 0
    errors: list[dict[str, str]] = []
    discovered = discover_local_files(source_dir)
    discovered_by_hash: dict[str, Path] = {}

    for path in discovered:
        try:
            discovered_by_hash.setdefault(sha256_file(path), path)
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})

    removed = 0
    relocated = 0
    for document in existing_documents:
        try:
            document_path = Path(document["path"]).resolve()
            managed = document_path.is_relative_to(source_dir)
        except (OSError, ValueError):
            managed = False
        if not managed:
            continue
        replacement = discovered_by_hash.get(document["sha256"])
        if replacement is None:
            db.delete_document(document["id"])
            existing_hashes.discard(document["sha256"])
            removed += 1
        elif replacement != document_path:
            db.update_document_path(document["id"], str(replacement), replacement.name)
            relocated += 1

    for path in discovered:
        curated_spec = curated.get(path)
        spec = curated_spec.model_copy(update={"path": str(path)}) if curated_spec else DocumentCreate(
            path=str(path),
            title=path.stem,
            jurisdiction="EU",
            authority="Local regulation library",
        )
        try:
            result = ingest_document(db, spec, source_dir)
            if result["sha256"] in existing_hashes:
                unchanged += 1
            else:
                imported += 1
                existing_hashes.add(result["sha256"])
        except (FileNotFoundError, ValueError) as exc:
            errors.append({"path": str(path), "error": str(exc)})

    return {
        "sourceDirectory": str(source_dir),
        "discovered": len(discovered),
        "imported": imported,
        "unchanged": unchanged,
        "removed": removed,
        "relocated": relocated,
        "errors": errors,
    }
