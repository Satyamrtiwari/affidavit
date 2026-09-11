"""
Legal Document Generation & Evaluation Agent — Backend Configuration

Loads environment variables and provides typed settings for the application.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# ─── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
REFERENCE_DOCS_DIR = BASE_DIR / "reference_docs"
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# Ensure output directory exists
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

# ─── Groq API ────────────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# ─── App Settings ─────────────────────────────────────────────────────────────
APP_ENV: str = os.getenv("APP_ENV", "development")
APP_DEBUG: bool = os.getenv("APP_DEBUG", "true").lower() == "true"

# ─── Generation Settings ─────────────────────────────────────────────────────
MAX_RETRIES: int = 2          # Self-correction loop retries
MIN_ACCEPTABLE_SCORE: int = 85  # Minimum evaluation score to accept without retry
LLM_TEMPERATURE: float = 0.2   # Low temperature for deterministic legal text
LLM_MAX_TOKENS: int = 4096     # Max tokens for generation


def validate_config() -> None:
    """Validate that required configuration is present."""
    if not GROQ_API_KEY:
        raise ValueError(
            "GROQ_API_KEY is not set. "
            "Copy .env.example to .env and add your Groq API key."
        )
