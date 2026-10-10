"""Allow the package to start with ``python -m bulk_rnaseq``."""

from bulk_rnaseq.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

