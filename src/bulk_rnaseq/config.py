"""Load the small application configuration used in Phase 1."""

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any
from collections.abc import Mapping
import os


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Host-only settings; no API key is stored in this object."""

    workspace_root: Path
    rscript_executable: str
    api_host: str
    api_port: int
    streamlit_host: str
    streamlit_port: int
    log_level: str


def load_runtime_config(*, environ: Mapping[str, str] | None = None) -> RuntimeConfig:
    """Process environment overrides local defaults; no implicit dotenv search.

    DeepSeek retains its existing, explicit .env loader. Service settings use the
    existing config module and Uvicorn/Streamlit's native environment names.
    """
    env = os.environ if environ is None else environ

    def value(name: str, default: str) -> str:
        result = env.get(name, default)
        if not result.strip() or any(ord(c) < 32 for c in result):
            raise ValueError(f"Invalid environment setting: {name}")
        return result

    def port(name: str, default: str) -> int:
        try:
            result = int(value(name, default))
            if not 1 <= result <= 65535:
                raise ValueError
            return result
        except ValueError:
            raise ValueError(f"Invalid port setting: {name}") from None

    level = value("RNASEQ_LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("Invalid environment setting: RNASEQ_LOG_LEVEL")
    return RuntimeConfig(
        Path(value("RNASEQ_WORKSPACE_ROOT", "workspace")).expanduser().resolve(),
        value("RNASEQ_RSCRIPT", "Rscript"), value("UVICORN_HOST", "127.0.0.1"),
        port("UVICORN_PORT", "8000"), value("STREAMLIT_SERVER_ADDRESS", "127.0.0.1"),
        port("STREAMLIT_SERVER_PORT", "8501"), level)


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated application settings needed during startup."""

    schema_version: int
    name: str
    environment: str
    log_level: str
    source_path: Path


def _required_string(section: dict[str, Any], key: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Configuration value 'app.{key}' must be a non-empty string.")
    return value.strip()


def load_config(config_path: Path) -> AppConfig:
    """Read and validate the Phase 1 TOML configuration file."""

    resolved_path = config_path.expanduser().resolve()
    with resolved_path.open("rb") as config_file:
        raw_config = tomllib.load(config_file)

    schema_version = raw_config.get("schema_version")
    if schema_version != 1:
        raise ValueError("Configuration 'schema_version' must be 1.")

    app_section = raw_config.get("app")
    if not isinstance(app_section, dict):
        raise ValueError("Configuration must contain an [app] section.")

    return AppConfig(
        schema_version=schema_version,
        name=_required_string(app_section, "name"),
        environment=_required_string(app_section, "environment"),
        log_level=_required_string(app_section, "log_level").upper(),
        source_path=resolved_path,
    )

