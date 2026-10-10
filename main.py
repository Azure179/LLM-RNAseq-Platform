"""Repository-level entry point for local development."""

from pathlib import Path

from bulk_rnaseq.cli import main as run_cli


def main() -> int:
    """Start the application with the repository's default configuration."""

    project_root = Path(__file__).resolve().parent
    default_config = project_root / "config" / "default.toml"
    return run_cli(default_config_path=default_config)


if __name__ == "__main__":
    raise SystemExit(main())

