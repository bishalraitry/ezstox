"""
Paths and settings.

Every path is anchored to the project folder, so ezstox works no matter
which directory you launch it from. API keys and preferences live in a
.env file in the project folder (git-ignored) and can be changed from the
in-app Settings screen, so nothing needs adding to ~/.zshrc.
"""

import os
from pathlib import Path

from dotenv import load_dotenv, set_key

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
CACHE_DIR = ROOT / ".cache"
ENV_FILE = ROOT / ".env"

# Any OpenAI-compatible API works; these are the presets offered in Settings.
PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "base_url": None,
        "key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-5-mini", "gpt-5"],
        "key_url": "platform.openai.com/api-keys",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "key_url": "platform.deepseek.com/api_keys",
    },
}

# Keys shown in Settings: (env var, label, what it's for)
API_KEYS = [
    ("OPENAI_API_KEY", "OpenAI", "AI analysis (OpenAI models)"),
    ("DEEPSEEK_API_KEY", "DeepSeek", "AI analysis (DeepSeek models)"),
    ("JINA_API_KEY", "Jina Reader", "faster article reading (optional)"),
]

CURRENCIES = ["USD", "GBP", "EUR", "CAD", "AUD", "CHF", "JPY"]
BENCHMARK = "SPY"

load_dotenv(ENV_FILE, override=False)


def get_setting(name, default=None):
    value = os.environ.get(name, "").strip()
    return value or default


def save_setting(name, value):
    """Persist a setting to .env and apply it to the running process."""
    ENV_FILE.touch(exist_ok=True)
    set_key(str(ENV_FILE), name, value, quote_mode="never")
    os.environ[name] = value


def base_currency():
    currency = get_setting("BASE_CURRENCY", "USD").upper()
    return currency if currency in CURRENCIES else "USD"


def ai_provider():
    """The configured provider, or the first one that has a key set."""
    chosen = get_setting("AI_PROVIDER")
    if chosen in PROVIDERS:
        return chosen
    for name, preset in PROVIDERS.items():
        if get_setting(preset["key_env"]):
            return name
    return "openai"


def ai_model(provider=None):
    preset = PROVIDERS[provider or ai_provider()]
    return get_setting(preset["model_env"], preset["default_model"])


def ai_key(provider=None):
    return get_setting(PROVIDERS[provider or ai_provider()]["key_env"])
