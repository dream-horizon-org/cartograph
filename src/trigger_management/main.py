"""Trigger Manager entry point."""

import logging
import sys

from shared.config import TRIGGER_POLL_INTERVAL
from shared.db import init_pool, close_pool
from shared.migrations import run_migrations
from trigger_management.trigger_loop import run_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Initializing trigger manager...")
    init_pool()
    logger.info("Running migrations...")
    run_migrations()
    logger.info("Migrations complete. Starting trigger loop...")
    try:
        run_loop(poll_interval=TRIGGER_POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        close_pool()


if __name__ == "__main__":
    main()
