import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///data/jt.db",
    )
    data_dir: Path = Path(os.getenv("DATA_DIR", "data")).resolve()
    embedding_backend: str = os.getenv("EMBEDDING_BACKEND", "hash")
    embedding_dimension: int = 1024
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-nomic-embed-text-v1.5")
    bge_model_path: str = os.getenv("BGE_MODEL_PATH", "model/bge-m3")
    llm_enabled: bool = os.getenv("LLM_ENABLED", "0") == "1"
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
    llm_model: str = os.getenv("LLM_MODEL", "qwen/qwen3.6-35b-a3b")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "180"))
    api_llm_base_url: str = os.getenv("API_LLM_BASE_URL", "").rstrip("/")
    api_llm_model: str = os.getenv("API_LLM_MODEL", "")
    api_llm_api_key: str = os.getenv("API_LLM_API_KEY", "")
    api_llm_allowed_hosts: tuple[str, ...] = tuple(
        host.strip().lower() for host in os.getenv("API_LLM_ALLOWED_HOSTS", "").split(",") if host.strip()
    )


settings = Settings()
