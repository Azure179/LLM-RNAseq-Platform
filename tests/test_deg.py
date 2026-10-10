"""Small Phase 2 tests that do not pretend to run DESeq2."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bulk_rnaseq.skills.deg import DegRunError, run_deg


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DegOrchestrationTests(unittest.TestCase):
    """Verify that a missing R executable produces an explicit failure."""

    def test_missing_rscript_is_not_reported_as_success(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            with self.assertRaises(DegRunError) as raised_error:
                run_deg(
                    count_matrix_path=PROJECT_ROOT / "examples" / "data" / "counts.csv",
                    metadata_path=PROJECT_ROOT / "examples" / "data" / "metadata.csv",
                    treatment="treatment",
                    control="control",
                    output_dir=Path(temporary_directory),
                    rscript_executable="definitely-not-a-real-rscript-executable",
                )

            self.assertIn("Unable to start Rscript", str(raised_error.exception))
            self.assertFalse((Path(temporary_directory) / "summary.json").exists())


if __name__ == "__main__":
    unittest.main()
