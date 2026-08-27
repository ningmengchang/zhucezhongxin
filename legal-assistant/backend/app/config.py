from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
DEFAULT_WORKSPACE = PROJECT_DIR.parent


def _codex_binary() -> str:
    explicit = os.getenv("CODEX_BINARY")
    if explicit:
        return explicit
    discovered = shutil.which("codex")
    if discovered:
        return discovered
    # The service may be launched through sudo/escalation with a restricted PATH.
    # Resolve the workspace owner's local Codex installation before falling back
    # to a relative command name.
    workspace_home = PROJECT_DIR.parents[2]
    candidate = workspace_home / ".local" / "bin" / "codex"
    if candidate.is_file():
        return str(candidate)
    return "codex"


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
    codex_binary: str = _codex_binary()
    codex_timeout_seconds: int = int(os.getenv("CODEX_TIMEOUT_SECONDS", "180"))
    codex_concurrency: int = int(os.getenv("CODEX_CONCURRENCY", "2"))
    queue_limit: int = int(os.getenv("CODEX_QUEUE_LIMIT", "20"))
    codex_mock: bool = _bool("CODEX_MOCK", False)
    codex_app_server_start_timeout_seconds: int = int(
        os.getenv("CODEX_APP_SERVER_START_TIMEOUT_SECONDS", "20")
    )
    codex_conversation_max_turns: int = int(
        os.getenv("CODEX_CONVERSATION_MAX_TURNS", "20")
    )
    codex_conversation_idle_seconds: int = int(
        os.getenv("CODEX_CONVERSATION_IDLE_SECONDS", "7200")
    )
    law_search_top_k: int = int(os.getenv("LAW_SEARCH_TOP_K", "8"))
    auto_seed: bool = _bool("AUTO_SEED", False)
    auto_sync_local_files: bool = _bool("AUTO_SYNC_LOCAL_FILES", True)
    research_total_timeout_seconds: int = int(os.getenv("RESEARCH_TOTAL_TIMEOUT_SECONDS", "480"))
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
