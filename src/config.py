"""
Paths and settings.

Every path is anchored to the project folder, so ezstox works no matter
which directory you launch it from. API keys live in a .env file in the
project folder (git-ignored) and can be set from the in-app Settings
screen, so nothing needs adding to ~/.zshrc.
"""

import os
from pathlib import Path

from dotenv import load_dotenv, set_key

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
ENV_FILE = ROOT / ".env"

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

# Keys the app knows about: (env var, label, what it's for, required?)
API_KEYS = [
    ("OPENAI_API_KEY", "OpenAI", "AI analysis", True),
    ("JINA_API_KEY", "Jina Reader", "faster article scraping (optional, free tier works without it)", False),
]

load_dotenv(ENV_FILE, override=False)


def get_setting(name, default=None):
    value = os.environ.get(name, "").strip()
    return value or default


def save_setting(name, value):
    """Persist a setting to .env and apply it to the running process."""
    ENV_FILE.touch(exist_ok=True)
    set_key(str(ENV_FILE), name, value, quote_mode="never")
    os.environ[name] = value


def openai_model():
    return get_setting("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
