"""
Application configuration loaded from environment variables / .env file.
All settings are prefixed with SPELLCHECK_ to avoid collisions.
"""
from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import List


class Settings(BaseSettings):
    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000

    # --- CORS ---
    # Accepts a JSON array string or a comma-separated string
    cors_origins: str = "*"

    # --- Paths ---
    hunspell_dict_dir: str = "/app/dictionaries"
    custom_dict_path: str = "/app/dictionaries/custom/org_dictionary.json"

    # --- Spell check defaults ---
    default_language: str = "he_IL"
    max_text_length: int = 200_000
    max_suggestions: int = 5

    # --- Logging ---
    log_level: str = "INFO"

    @property
    def cors_origins_list(self) -> List[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    model_config = {"env_prefix": "SPELLCHECK_", "env_file": ".env"}


settings = Settings()
