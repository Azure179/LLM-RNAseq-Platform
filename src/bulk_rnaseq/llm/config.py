"""Explicit dotenv path + environment overrides; never search parent directories for secrets."""

from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from bulk_rnaseq.llm.exceptions import DeepSeekError


@dataclass(frozen=True, slots=True)
class DeepSeekConfig:
    api_key: str = field(repr=False)
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    timeout_seconds: float = 30
    max_tokens: int = 64

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise DeepSeekError("LLM_KEY_MISSING", "Set DEEPSEEK_API_KEY in your local .env or environment.")
        if any(c.isspace() or ord(c) < 32 or ord(c) > 126 for c in self.api_key):
            raise DeepSeekError("LLM_CONFIG_ERROR", "API key must contain printable ASCII without whitespace.")
        try:
            url = urlsplit(self.base_url)
            allowed = (url.scheme == "https" and url.hostname == "api.deepseek.com" and url.port in (None, 443)
                       and not url.username and not url.password and not url.query and not url.fragment
                       and url.path.rstrip("/") in ("", "/v1"))
        except (ValueError, TypeError, AttributeError):
            allowed = False
        if not allowed:
            raise DeepSeekError("LLM_CONFIG_ERROR", "Use the official HTTPS DeepSeek base URL, optionally ending in /v1; proxies/custom hosts are unsupported.")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        if not isinstance(self.model, str) or not self.model.strip() or any(c.isspace() for c in self.model) or self.api_key in self.model:
            raise DeepSeekError("LLM_CONFIG_ERROR", "DEEPSEEK_MODEL must be a non-empty model identifier.")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 120:
            raise DeepSeekError("LLM_CONFIG_ERROR", "Timeout must be finite and in (0, 120] seconds.")
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int) or not 1 <= self.max_tokens <= 1024:
            raise DeepSeekError("LLM_CONFIG_ERROR", "Phase 6.0 max_tokens must be an integer in [1, 1024].")


def load_deepseek_config(env_file: Path | None = None, *, environ: Mapping[str, str] | None = None) -> DeepSeekConfig:
    """Default .env is in cwd; process environment wins, including explicitly empty values."""
    path = Path.cwd() / ".env" if env_file is None else Path(env_file).expanduser().resolve()
    try:
        values = dotenv_values(path, encoding="utf-8-sig", interpolate=False) if path.exists() else {}
    except (OSError, UnicodeError):
        raise DeepSeekError("LLM_CONFIG_ERROR", "Unable to read the selected .env file.") from None
    values.update(os.environ if environ is None else environ)
    try:
        timeout = float(values.get("DEEPSEEK_TIMEOUT_SECONDS", "30"))
        max_tokens = int(values.get("DEEPSEEK_MAX_TOKENS", "64"))
    except (ValueError, TypeError):
        raise DeepSeekError("LLM_CONFIG_ERROR", "Invalid timeout or token limit configuration.") from None
    config = DeepSeekConfig(api_key=values.get("DEEPSEEK_API_KEY") or "",
                            base_url=values.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                            model=values.get("DEEPSEEK_MODEL", "deepseek-flash"),
                            timeout_seconds=timeout, max_tokens=max_tokens)
    logging.getLogger(__name__).info("DeepSeek configuration loaded; credentials are not logged.")
    return config
