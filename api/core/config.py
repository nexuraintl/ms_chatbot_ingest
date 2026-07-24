from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Gobernanza / observabilidad (GOB-GCP-STD-01)
    service_name: str = "ms_chatbot_ingest"
    service_version: str = "1.0.0"
    environment: str = "dev"
    log_level: str = "INFO"
    google_cloud_project: str = ""

    # Gemini File Search Store
    gemini_api_key: str

    # Firestore (registro de tenants y tracking de documentos ingestados)
    gcp_project: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
