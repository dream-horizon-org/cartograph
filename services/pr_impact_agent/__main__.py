"""Entrypoint: `python -m services.pr_impact_agent`."""

from __future__ import annotations

import logging
import os

import uvicorn


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    port = int(os.getenv("PR_IMPACT_AGENT_PORT", "8300"))
    uvicorn.run(
        "services.pr_impact_agent.app:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
