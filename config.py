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


def openrouter_api_key():
    return _require("OPENROUTER_API_KEY")


def openrouter_api_key_alt():
    return _require("OPENROUTER_API_KEY_ALT")


def openrouter_api_keys():
    """Return configured OpenRouter keys in primary/alternate order.

    A revoked or expired primary key should not prevent a configured
    alternate key from serving the request. Missing optional candidates are
    skipped; callers get one clear error only when none are configured.
    """
    keys = []

    for name in ("OPENROUTER_API_KEY", "OPENROUTER_API_KEY_ALT"):
        try:
            value = _require(name)
        except RuntimeError:
            continue

        if value not in keys:
            keys.append(value)

    if not keys:
        raise RuntimeError(
            "No OpenRouter API key is configured. Set OPENROUTER_API_KEY "
            "or OPENROUTER_API_KEY_ALT in .env."
        )

    return keys


def ollama_api_key():
    return _require("OLLAMA_API_KEY")


# ============================================================
# MODELS
# ============================================================

# The only vision path. Other free vision slugs were tried and rejected:
# inclusionai/ling-3.0-flash-vl
# (:free) now answers 404 "unavailable for free" (paid slug drops the suffix),
# gemma-4-31b-it:free, gemma-4-26b-a4b-it:free and qwen3.8-27b:free answered 429
# on every attempt, thinkingmachines/inkling:free is 403 (agentic harnesses
# only), and dots-3-note-preview:free spends its whole budget narrating a
# "Thinking Process" and never emits the description.
VISION_OPENROUTER_MODELS = [
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

# Verified: responds in ~1s at zero cost, and supports the OpenRouter web plugin.
REASONING_MODEL = "nex-agi/nex-n2.5-pro:free"

CODING_MODEL = "gpt-oss:20b"


# ============================================================
# ENDPOINTS
# ============================================================

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OLLAMA_HOST = "https://ollama.com"

OPENROUTER_APP_TITLE = "Sovereign AI Workbench"


# ============================================================
# RETRY / TIMEOUT TUNING
# ============================================================

VISION_ATTEMPTS_PER_MODEL = 2
VISION_BACKOFF_SECONDS = 2.0
VISION_TIMEOUT_SECONDS = 150.0

# Hard ceiling on the whole vision stage. A hanging request is only caught by
# the per-request timeout, so this bounds the total time spent retrying.
VISION_TOTAL_BUDGET_SECONDS = 360.0

OPENROUTER_TIMEOUT_SECONDS = 180

# Reasoning models spend tokens on hidden reasoning before emitting content;
# keep this budget generous so the final answer is not truncated.
LLM_MAX_TOKENS = 16000


# ============================================================
# STORAGE
# ============================================================

KNOWLEDGE_DB = PROJECT_ROOT / "knowledge.db"
