"""Application logging setup for Atlas."""

import logging


def setup_logging(level: str = "info") -> logging.Logger:
    """Configure root logging and return the atlas logger."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        force=True,
    )
    return logging.getLogger("atlas")
