from __future__ import annotations

from datetime import date

from .config import settings
from .database import Database
from .ingestion import ingest_document
from .models import DocumentCreate


SEED_DOCUMENTS = [
    DocumentCreate(
        path="欧盟新电池法案.pdf",
        title="Regulation (EU) 2023/1542 — Batteries and waste batteries",
        authority="European Parliament and Council",
        official_url="https://eur-lex.europa.eu/eli/reg/2023/1542/oj/eng",
        publication_date=date(2023, 7, 28),
        effective_date=date(2023, 8, 17),
    ),
    DocumentCreate(
        path="法规依据/Regulation_EU_2023_1542_consolidated_2025-07-31_EN.pdf",
        title="Regulation (EU) 2023/1542 — Consolidated text 2025-07-31",
        authority="EUR-Lex",
        official_url="https://eur-lex.europa.eu/eli/reg/2023/1542/2025-07-31/eng",
        publication_date=date(2025, 7, 31),
        effective_date=date(2025, 7, 31),
    ),
    DocumentCreate(
        path="ESPR (EU)2024 1781.pdf",
        title="Regulation (EU) 2024/1781 — Ecodesign for Sustainable Products",
        authority="European Parliament and Council",
        official_url="https://eur-lex.europa.eu/eli/reg/2024/1781/oj/eng",
        publication_date=date(2024, 6, 28),
        effective_date=date(2024, 7, 18),
    ),
    DocumentCreate(
        path="法规依据/Regulation_EU_2024_1781_Corrigendum_2024-08-07_EN.pdf",
        title="Corrigendum to Regulation (EU) 2024/1781 — 2024-08-07",
        authority="Official Journal of the European Union",
        official_url="https://eur-lex.europa.eu/eli/reg/2024/1781/corrigendum/2024-08-07/oj",
        publication_date=date(2024, 8, 7),
        effective_date=date(2024, 8, 7),
    ),
    DocumentCreate(
        path="20260717 注册中心正式条例 OJ_L_202601778_EN_TXT.pdf",
        title="Commission Implementing Regulation (EU) 2026/1778 — DPP Registry",
        authority="European Commission",
        official_url="https://eur-lex.europa.eu/eli/reg_impl/2026/1778/oj/eng",
        publication_date=date(2026, 7, 17),
        effective_date=date(2026, 8, 6),
    ),
    DocumentCreate(
        path="20260527-欧盟DPP实践指南.pdf",
        title="European Commission DPP practical guide — 2026-05-27",
        authority="European Commission",
        publication_date=date(2026, 5, 27),
        effective_date=date(2026, 5, 27),
    ),
    DocumentCreate(
        path="DPP注册中心原文.pdf",
        title="Draft DPP registry working document",
        authority="Draft — not adopted",
    ),
]


def seed(database: Database | None = None) -> list[dict]:
    settings.ensure_directories()
    db = database or Database(settings.database_path)
    db.init()
    results = []
    for document in SEED_DOCUMENTS:
        try:
            results.append(ingest_document(db, document, settings.source_dir, replace=True))
        except FileNotFoundError as exc:
            results.append({"path": document.path, "error": str(exc)})
    return results


if __name__ == "__main__":
    for result in seed():
        print(result.get("id", "SKIP"), result.get("title") or result.get("path"), result.get("error", ""))
