"""Load the small application configuration used in Phase 1."""

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any


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

