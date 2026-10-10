"""Smoke tests for the Phase 1 startup path."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bulk_rnaseq.cli import main
from bulk_rnaseq.config import load_config


VALID_CONFIG = """\
schema_version = 1

[app]
name = "test-bulk-rnaseq"
environment = "test"
log_level = "INFO"
"""


class StartupTests(unittest.TestCase):
    """Verify configuration loading and a successful no-analysis startup."""

    def test_load_config_uses_pathlib_path(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "test.toml"
            config_path.write_text(VALID_CONFIG, encoding="utf-8")

            config = load_config(config_path)

        self.assertEqual(config.name, "test-bulk-rnaseq")
        self.assertEqual(config.environment, "test")
        self.assertIsInstance(config.source_path, Path)

    def test_cli_starts_without_running_an_analysis(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "test.toml"
            config_path.write_text(VALID_CONFIG, encoding="utf-8")

            exit_code = main(["--config", str(config_path)])

        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()

