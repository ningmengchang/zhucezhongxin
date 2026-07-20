from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
DEFAULT_WORKSPACE = PROJECT_DIR.parent


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    listen_host: str = os.getenv("LEGAL_ASSISTANT_HOST", "192.168.110.79")
    listen_port: int = int(os.getenv("LEGAL_ASSISTANT_PORT", "8090"))
    data_dir: Path = Path(os.getenv("LEGAL_ASSISTANT_DATA_DIR", BACKEND_DIR / "data"))
    source_dir: Path = Path(os.getenv("LEGAL_ASSISTANT_SOURCE_DIR", DEFAULT_WORKSPACE))
    database_path: Path = Path(
        os.getenv("LEGAL_ASSISTANT_DATABASE", BACKEND_DIR / "data" / "legal_assistant.sqlite3")
    )
    codex_binary: str = os.getenv("CODEX_BINARY", shutil.which("codex") or "codex")
    codex_timeout_seconds: int = int(os.getenv("CODEX_TIMEOUT_SECONDS", "180"))
    codex_concurrency: int = int(os.getenv("CODEX_CONCURRENCY", "2"))
    queue_limit: int = int(os.getenv("CODEX_QUEUE_LIMIT", "20"))
    conversation_memory_turns: int = int(os.getenv("CONVERSATION_MEMORY_TURNS", "5"))
    codex_verify_enabled: bool = _bool("CODEX_VERIFY_ENABLED", True)
    codex_mock: bool = _bool("CODEX_MOCK", False)
    law_retrieval_mode: str = os.getenv("LAW_RETRIEVAL_MODE", "mcp").strip().lower()
    law_search_top_k: int = int(os.getenv("LAW_SEARCH_TOP_K", "8"))
    law_search_max_calls: int = int(os.getenv("LAW_SEARCH_MAX_CALLS", "4"))
    law_read_max_calls: int = int(os.getenv("LAW_READ_MAX_CALLS", "8"))
    auto_seed: bool = _bool("AUTO_SEED", False)
    auto_sync_local_files: bool = _bool("AUTO_SYNC_LOCAL_FILES", True)
    max_evidence: int = int(os.getenv("MAX_EVIDENCE", "24"))
    research_local_rounds: int = int(os.getenv("RESEARCH_LOCAL_ROUNDS", "3"))
    research_web_rounds: int = int(os.getenv("RESEARCH_WEB_ROUNDS", "1"))
    research_total_timeout_seconds: int = int(os.getenv("RESEARCH_TOTAL_TIMEOUT_SECONDS", "480"))
    research_max_evidence: int = int(os.getenv("RESEARCH_MAX_EVIDENCE", "60"))
    web_search_enabled: bool = _bool("WEB_SEARCH_ENABLED", True)
    web_max_sources: int = int(os.getenv("WEB_MAX_SOURCES", "5"))
    web_official_domains: tuple[str, ...] = tuple(
        item.strip().lower().lstrip(".")
        for item in os.getenv(
            "WEB_OFFICIAL_DOMAINS",
            "europa.eu,eur-lex.europa.eu,op.europa.eu,echa.europa.eu",
        ).split(",")
        if item.strip()
    )
    report_expansion_enabled: bool = _bool("REPORT_EXPANSION_ENABLED", True)
    cors_origins: tuple[str, ...] = tuple(
        item.strip()
        for item in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,http://192.168.110.79:5173,http://192.168.110.79:8090",
        ).split(",")
        if item.strip()
    )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "jobs").mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
