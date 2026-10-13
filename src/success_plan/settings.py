"""Environment-backed settings. Secrets and contact details come from env vars, never from code."""

import os
import re

from dotenv import load_dotenv


class SettingsError(Exception):
    pass


def load_env() -> None:
    """Load a local .env if present. Existing environment variables win."""
    load_dotenv(override=False)


def sec_user_agent() -> str:
    """EDGAR requires a descriptive User-Agent with a name and contact email."""
    value = os.environ.get("SEC_USER_AGENT", "").strip()
    if not value:
        raise SettingsError(
            "SEC_USER_AGENT is not set. Add it to .env (see .env.example), "
            'for example: SEC_USER_AGENT="Jane Doe jane@example.com"'
        )
    if not re.search(r"\S+@\S+\.\S+", value) or " " not in value:
        raise SettingsError(
            "SEC_USER_AGENT must include a name and a contact email, "
            'for example: "Jane Doe jane@example.com"'
        )
    return value
