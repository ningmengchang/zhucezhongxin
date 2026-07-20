from datetime import date
from pathlib import Path

from app.database import Database
from app.ingestion import ingest_document
from app.models import DocumentCreate, ReviewStatus, SourceLevel
from app.retrieval import Retriever, prepare_context_queries


def test_ingest_and_retrieve_primary_evidence(tmp_path: Path):
    source = tmp_path / "regulation.txt"
    source.write_text(
        "Article 15\nCustoms authorities shall verify the unique registration identifier and commodity code.",
        encoding="utf-8",
    )
    database = Database(tmp_path / "test.sqlite3")
    database.init()
    document = ingest_document(
        database,
        DocumentCreate(
            path=str(source),
            title="Test Regulation",
            source_level=SourceLevel.A1,
            review_status=ReviewStatus.published,
            effective_date=date(2020, 1, 1),
        ),
        tmp_path,
    )

    results = Retriever(database).search("customs registration identifier commodity code")

    assert document["passage_count"] == 1
    assert results
    assert results[0].sourceLevel == SourceLevel.A1
    assert "unique registration identifier" in results[0].quote


def test_published_future_regulation_is_included_and_labelled_by_default(tmp_path: Path):
    source = tmp_path / "future.txt"
    source.write_text("Article 1\nA future registry requirement.", encoding="utf-8")
    database = Database(tmp_path / "test.sqlite3")
    database.init()
    ingest_document(
        database,
        DocumentCreate(
            path=str(source),
            source_level=SourceLevel.A1,
            review_status=ReviewStatus.published,
            effective_date=date(2999, 1, 1),
        ),
        tmp_path,
    )

    future = Retriever(database).search("future registry requirement")
    assert future and future[0].applicabilityStatus == "future"
    assert Retriever(database).search(
        "future registry requirement", include_future_rules=False
    ) == []
    current = Retriever(database).search("future registry requirement", as_of_date=date(2999, 1, 2))
    assert current and current[0].applicabilityStatus == "current"


def test_not_yet_published_regulation_is_not_time_travelled(tmp_path: Path):
    source = tmp_path / "unpublished-at-cutoff.txt"
    source.write_text("Article 1\nA registry API requirement.", encoding="utf-8")
    database = Database(tmp_path / "test.sqlite3")
    database.init()
    ingest_document(
        database,
        DocumentCreate(
            path=str(source),
            source_level=SourceLevel.A1,
            review_status=ReviewStatus.published,
            publication_date=date(2030, 1, 1),
            effective_date=date(2030, 2, 1),
        ),
        tmp_path,
    )

    assert Retriever(database).search(
        "registry API", as_of_date=date(2029, 12, 31)
    ) == []


def test_api_query_recalls_registry_and_semantic_repository_rules(tmp_path: Path):
    source = tmp_path / "registry-api.txt"
    source.write_text(
        "Article 3\n"
        "An API for registering the digital product passport and receiving information from the registry.\n"
        "Article 8\n"
        "The actor shall register through the API and receive the unique registration identifier in the API response.\n"
        "Article 9\n"
        "The proof of registration shall be available to the verified economic operator through the API.\n"
        "Article 12\n"
        "The semantic repository shall include a search service. Its content shall be accessible through publicly documented APIs supporting common data formats.",
        encoding="utf-8",
    )
    database = Database(tmp_path / "test.sqlite3")
    database.init()
    ingest_document(
        database,
        DocumentCreate(
            path=str(source),
            title="Implementing Registry Regulation",
            source_level=SourceLevel.A1,
            review_status=ReviewStatus.published,
            publication_date=date(2026, 7, 17),
            effective_date=date(2026, 8, 6),
        ),
        tmp_path,
    )
    baseline = tmp_path / "espr.txt"
    baseline.write_text(
        "Article 13\nBy 19 July 2026, the Commission shall set up a digital registry which stores unique identifiers.",
        encoding="utf-8",
    )
    ingest_document(
        database,
        DocumentCreate(
            path=str(baseline),
            title="Regulation (EU) 2024/1781",
            source_level=SourceLevel.A1,
            review_status=ReviewStatus.published,
            publication_date=date(2024, 6, 28),
            effective_date=date(2024, 7, 18),
        ),
        tmp_path,
    )

    results = Retriever(database).search(
        "注册中心定义的api有哪些", as_of_date=date(2026, 7, 20), limit=12
    )
    text = " ".join(item.quote for item in results)
    assert "registering the digital product passport" in text
    assert "API response" in text
    assert "proof of registration" in text
    assert "publicly documented APIs" in text
    assert "search service" in text
    assert "shall set up a digital registry" in text
    assert {item.applicabilityStatus for item in results} == {"current", "future"}


def test_chinese_query_expands_to_english_legal_terms(tmp_path: Path):
    source = tmp_path / "customs.txt"
    source.write_text(
        "Article 15\nCustoms authorities shall verify the unique registration identifier and commodity code.",
        encoding="utf-8",
    )
    database = Database(tmp_path / "test.sqlite3")
    database.init()
    ingest_document(
        database,
        DocumentCreate(
            path=str(source),
            source_level=SourceLevel.A1,
            review_status=ReviewStatus.published,
            effective_date=date(2020, 1, 1),
        ),
        tmp_path,
    )

    results = Retriever(database).search("海关查询注册中心需要什么唯一登记标识符和商品编码")
    assert results
    assert "Customs authorities" in results[0].quote


def test_near_duplicate_memory_cannot_drown_authoritative_identifier_evidence(tmp_path: Path):
    formal = tmp_path / "battery-regulation.txt"
    formal.write_text(
        "Article 3\nA unique identifier means a unique string of characters for the identification of batteries "
        "that also enables a web link to the battery passport.\n\n"
        "Article 77\nThe battery passport shall be accessible through the QR code which links to a unique identifier "
        "that the economic operator placing the battery on the market shall attribute to it.",
        encoding="utf-8",
    )
    summary = tmp_path / "internal-summary.md"
    summary.write_text(
        "电池唯一标识符和电池护照唯一标识符。电池唯一标识和电池护照唯一标识。" * 20,
        encoding="utf-8",
    )
    database = Database(tmp_path / "test.sqlite3")
    database.init()
    ingest_document(
        database,
        DocumentCreate(
            path=str(formal),
            title="Battery Regulation",
            source_level=SourceLevel.A1,
            review_status=ReviewStatus.published,
            effective_date=date(2023, 8, 17),
        ),
        tmp_path,
    )
    ingest_document(
        database,
        DocumentCreate(
            path=str(summary),
            title="Internal summary",
            source_level=SourceLevel.B,
            review_status=ReviewStatus.published,
        ),
        tmp_path,
    )

    contexts = ["电池唯一标识和电池护照唯一标识"]
    results = Retriever(database).search(
        "电池唯一标识符和电池护照唯一标识符", context_queries=contexts, limit=12
    )

    assert prepare_context_queries("电池唯一标识符和电池护照唯一标识符", contexts) == []
    assert any(item.sourceLevel == SourceLevel.A1 for item in results)
    assert any("identification of batteries" in item.quote for item in results)


def test_referential_comparison_uses_identifier_context_without_merging_queries(tmp_path: Path):
    formal = tmp_path / "battery-regulation.txt"
    formal.write_text(
        "A unique identifier means a unique string of characters for the identification of batteries "
        "that also enables a web link to the battery passport.",
        encoding="utf-8",
    )
    database = Database(tmp_path / "test.sqlite3")
    database.init()
    ingest_document(
        database,
        DocumentCreate(
            path=str(formal),
            source_level=SourceLevel.A1,
            review_status=ReviewStatus.published,
            effective_date=date(2023, 8, 17),
        ),
        tmp_path,
    )

    results = Retriever(database).search(
        "那二者有什么区别",
        context_queries=["电池唯一标识符和电池护照唯一标识符"],
        limit=12,
    )

    assert results
    assert results[0].sourceLevel == SourceLevel.A1
    assert "battery passport" in results[0].quote
