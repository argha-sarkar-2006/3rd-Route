"""Central configuration: API keys from the environment and model definitions.

Keys are read lazily via functions so that importing this module never fails
because one unrelated key is missing.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

load_dotenv(PROJECT_ROOT / ".env")


# ============================================================
# API KEYS
# ============================================================

def _require(name):
    value = (os.getenv(name) or "").strip()

    if not value or value.startswith("PASTE_"):
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )

    return value


def ollama_api_key():
    """Return the optional Ollama key.

    Local Ollama does not need a key; the accessor remains available for a
    configured authenticated endpoint.
    """
    return (os.getenv("OLLAMA_API_KEY") or "").strip()


# ============================================================
# MODELS
# ============================================================

# All stages use models downloaded into the local Ollama daemon.
KNOWLEDGE_MODEL = os.getenv("OLLAMA_KNOWLEDGE_MODEL", "qwen3.5:2b")
REASONING_MODEL = os.getenv("OLLAMA_REASONING_MODEL", "qwen3.5:2b")
VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "qwen3.5:2b")
CODING_MODEL = os.getenv("OLLAMA_CODING_MODEL", "aikid123/qwen3-coder:0.6b")


# ============================================================
# ENDPOINTS
# ============================================================

# Local-only runtime: do not route model requests to a hosted endpoint.
OLLAMA_HOST = "http://localhost:11434"


# ============================================================
# RETRY / TIMEOUT TUNING
# ============================================================

VISION_ATTEMPTS_PER_MODEL = 2
VISION_BACKOFF_SECONDS = 2.0
VISION_TIMEOUT_SECONDS = 150.0

# Hard ceiling on the whole vision stage. A hanging request is only caught by
# the per-request timeout, so this bounds the total time spent retrying.
VISION_TOTAL_BUDGET_SECONDS = 360.0

OLLAMA_TIMEOUT_SECONDS = 180

# Reasoning models spend tokens on hidden reasoning before emitting content;
# keep this budget generous so the final answer is not truncated.
LLM_MAX_TOKENS = 16000


# ============================================================
# STORAGE
# ============================================================

KNOWLEDGE_DB = PROJECT_ROOT / "knowledge.db"
