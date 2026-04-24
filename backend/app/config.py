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
    cors_origins: str = "*"

    # --- Paths ---
    hunspell_dict_dir: str = "/app/dictionaries"
    custom_dict_path: str = "/app/dictionaries/custom/org_dictionary.json"

    # --- Language control ---
    # Comma-separated list of BCP-47 codes to enable, e.g. "he-IL,en-US,ar-SA".
    # Leave empty to auto-discover every .aff/.dic pair in hunspell_dict_dir.
    languages: str = ""

    # --- Spell check defaults ---
    default_language: str = "he_IL"
    max_text_length: int = 200_000
    max_suggestions: int = 5

    # --- MongoDB (optional) ---
    # When set, the organisational dictionary is stored in MongoDB instead of JSON.
    # Format: mongodb://user:pass@host:27017  or  mongodb+srv://...
    mongo_uri: str = ""
    mongo_db: str = "spellcheck"

    # --- Logging ---
    log_level: str = "INFO"

    @property
    def cors_origins_list(self) -> List[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def enabled_languages(self) -> List[str]:
        """List of explicitly enabled language codes, or [] to auto-discover all."""
        if not self.languages.strip():
            return []
        return [lang.strip() for lang in self.languages.split(",") if lang.strip()]

    model_config = {"env_prefix": "SPELLCHECK_", "env_file": ".env"}


settings = Settings()
