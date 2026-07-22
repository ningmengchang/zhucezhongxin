import asyncio

from app.web_evidence import WebEvidenceValidator


class InMemoryValidator(WebEvidenceValidator):
    def __init__(self, page: str):
        super().__init__()
        self.page = page

    async def _fetch(self, url: str):
        return url, self.page.encode("utf-8"), "text/html; charset=utf-8"


def candidate(quote: str) -> dict:
    return {
        "title": "Primary source",
        "url": "https://example.com/source",
        "publisher": "Example authority",
        "quote": quote,
        "publicationDate": "2026-01-01",
        "effectiveDate": None,
    }


def test_verified_web_quote_is_accepted(monkeypatch):
    quote = "This exact regulatory sentence is present in the published source."
    monkeypatch.setattr("app.web_evidence._public_addresses", lambda _host: True)
    accepted, rejected = asyncio.run(
        InMemoryValidator(f"<html><body>{quote}</body></html>").validate_many([candidate(quote)])
    )
    assert len(accepted) == 1
    assert accepted[0].origin == "web"
    assert accepted[0].quote == quote
    assert rejected == []


def test_web_quote_missing_from_page_is_rejected(monkeypatch):
    monkeypatch.setattr("app.web_evidence._public_addresses", lambda _host: True)
    accepted, rejected = asyncio.run(
        InMemoryValidator("Different page content").validate_many(
            [candidate("This claimed quotation does not occur on the source page.")]
        )
    )
    assert accepted == []
    assert "逐字核对" in rejected[0]


def test_non_public_address_is_rejected(monkeypatch):
    monkeypatch.setattr("app.web_evidence._public_addresses", lambda _host: False)
    accepted, rejected = asyncio.run(
        InMemoryValidator("unused").validate_many(
            [candidate("A sufficiently long quotation for validation purposes.")]
        )
    )
    assert accepted == []
    assert "公共网络" in rejected[0]
