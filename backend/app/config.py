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
    # Reused as the one-time seed source when migrating into MongoDB.
    custom_dict_path: str = "/app/dictionaries/custom/org_dictionary.json"

    # --- MongoDB (custom dictionary store) ---
    mongo_uri: str = "mongodb://mongo:27017"
    mongo_db: str = "spellcheck"
    mongo_collection: str = "custom_dictionary"
    # How often each worker reloads the dictionary cache from Mongo.
    # Set to 0 to disable the periodic refresh.
    dict_refresh_interval_minutes: float = 5.0

    # --- Spell check defaults ---
    default_language: str = "he_IL"
    max_text_length: int = 200_000
    max_suggestions: int = 5

    # --- Logging ---
    log_level: str = "INFO"

    # --- External approval service ---
    # URL that receives {word, context} when a user clicks "הצע למילון"
    approveit_url: str = ""

    @property
    def cors_origins_list(self) -> List[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    model_config = {"env_prefix": "SPELLCHECK_", "env_file": ".env"}


settings = Settings()
