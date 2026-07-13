from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    app_name: str = "PrivShield API"
    app_env: str = "development"
    database_path: Path = BASE_DIR / "data" / "privshield.db"
    database_timeout_seconds: float = 10.0
    ner_enabled: bool = False
    ner_model: str = "Davlan/xlm-roberta-base-ner-hrl"
    ner_device: int = -1
    ner_threshold: float = 0.70
    semantic_model_enabled: bool = False
    semantic_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    semantic_model_device: int = -1
    knowledge_graph_remote_enabled: bool = False
    knowledge_graph_cnprobase_url: str = "http://shuyantech.com/api/cnprobase/concept"
    knowledge_graph_cndbpedia_url: str = "http://shuyantech.com/api/cndbpedia/avpair"
    knowledge_graph_timeout_seconds: float = 4.0
    knowledge_graph_cache_seconds: int = 86_400
    llm_enabled: bool = False
    llm_base_url: str = "http://127.0.0.1:8001/v1"
    llm_api_key: str = "local-token"
    llm_model: str = "Qwen/Qwen3-14B-AWQ"
    llm_timeout_seconds: float = 45.0
    llm_max_retries: int = 1
    llm_max_routed_sentences: int = 8
    llm_max_concurrency: int = 2
    max_upload_bytes: int = 20_000_000
    max_batch_records: int = 500
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PRIVSHIELD_", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


settings = Settings()
