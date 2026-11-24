"""Single API process, configured by the shared host environment settings."""

import uvicorn
from bulk_rnaseq.config import load_runtime_config


def main() -> None:
    config = load_runtime_config()
    uvicorn.run("bulk_rnaseq.api.main:app", host=config.api_host,
                port=config.api_port, log_level=config.log_level.lower())


if __name__ == "__main__":
    main()
