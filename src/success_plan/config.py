"""Loaders for files under config/."""

from pathlib import Path

import yaml

from .paths import PROJECT_ROOT

ALIASES_FILE = PROJECT_ROOT / "config" / "company_aliases.yaml"
NEWS_SOURCES_FILE = PROJECT_ROOT / "config" / "news_sources.yaml"
PRIVATE_NAMES_FILE = PROJECT_ROOT / "config" / "private_names.txt"  # gitignored


def load_private_names(path: Path = PRIVATE_NAMES_FILE) -> list[str]:
    """Names of people to redact from stored headlines (one per line; # comments). Empty if the file is missing."""
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.strip().startswith("#")]


def load_low_signal_domains(path: Path = NEWS_SOURCES_FILE) -> list[str]:
    """Domains whose headlines are kept for review but excluded from Extract. Empty if the file is missing."""
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    return [str(d).strip().lower() for d in data.get("low_signal_domains") or [] if str(d).strip()]


def load_brand_aliases(cik: int, path: Path = ALIASES_FILE) -> list[str] | None:
    """Brand names for a company, or None if the company has no entry."""
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text()) or {}
    entry = (data.get("companies") or {}).get(str(cik))
    if entry is None:
        return None
    return [str(b) for b in entry.get("brands") or []]
