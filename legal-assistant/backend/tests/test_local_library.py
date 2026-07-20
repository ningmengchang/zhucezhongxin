from app.database import Database
from app.ingestion import ingest_document
from app.local_library import discover_local_files, sync_local_library
from app.models import DocumentCreate, ReviewStatus, SourceLevel


def test_discovery_uses_local_supported_files_and_excludes_application_files(tmp_path):
    (tmp_path / "regulation.md").write_text("Article 1\nLocal rule", encoding="utf-8")
    evidence_dir = tmp_path / "法规依据"
    evidence_dir.mkdir()
    (evidence_dir / "standard.txt").write_text("A local standard", encoding="utf-8")
    (evidence_dir / "README.md").write_text("technical notes", encoding="utf-8")
    app_dir = tmp_path / "legal-assistant"
    app_dir.mkdir()
    (app_dir / "README.md").write_text("application docs", encoding="utf-8")

    discovered = {path.name for path in discover_local_files(tmp_path)}

    assert discovered == {"regulation.md", "standard.txt"}


def test_sync_imports_new_local_files_as_published_b_sources(tmp_path):
    (tmp_path / "local-rule.md").write_text("Article 1\nA local compliance rule.", encoding="utf-8")
    database = Database(tmp_path / "library.sqlite3")

    first = sync_local_library(database, tmp_path)
    second = sync_local_library(database, tmp_path)
    documents = database.list_documents()

    assert first["discovered"] == 1 and first["imported"] == 1
    assert second["imported"] == 0 and second["unchanged"] == 1
    assert documents[0]["source_level"] == "B"
    assert documents[0]["review_status"] == "published"


def test_upload_and_web_archive_routes_are_not_exposed():
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/api/documents/upload" not in paths
    assert "/api/web-discovery/archive" not in paths
    assert "/api/admin/sync-local" in paths


def test_scanned_pdf_uses_local_ocr_fallback(tmp_path, monkeypatch):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"fake-pdf-for-mocked-extractors")
    monkeypatch.setattr("app.ingestion.extract_pdf", lambda _: [])
    monkeypatch.setattr(
        "app.ingestion.extract_pdf_ocr",
        lambda _: [
            {
                "article": "Article 1",
                "paragraph": None,
                "page": 1,
                "text": "OCR extracted local regulation text.",
                "position": 0,
            }
        ],
    )
    database = Database(tmp_path / "ocr.sqlite3")
    database.init()

    document = ingest_document(
        database,
        DocumentCreate(
            path=str(source),
            source_level=SourceLevel.B,
            review_status=ReviewStatus.published,
        ),
        tmp_path,
    )

    assert document["passage_count"] == 1
    assert database.published_passages()[0]["text"] == "OCR extracted local regulation text."
