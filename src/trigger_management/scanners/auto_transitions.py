"""Auto-transitions — system-level state changes that don't require an agent."""

import logging

from shared.config import MERGE_CONFIDENCE_THRESHOLD, REJECT_CONFIDENCE_THRESHOLD
from shared.db import execute_mutate

logger = logging.getLogger(__name__)


def run_auto_transitions() -> int:
    """Run all auto-transitions. Returns count of transitions made."""
    count = 0
    count += _auto_escalate_to_resolver()
    count += _auto_reject()
    return count


def _auto_escalate_to_resolver() -> int:
    """Both confidence scores > threshold AND r_conf IS NULL → set status = 'R'."""
    rowcount = execute_mutate(
        """UPDATE consolidations
           SET status = 'R', updated_at = now()
           WHERE status IN ('B1', 'B2')
             AND a_conf_score >= %s
             AND b_conf_score >= %s
             AND r_conf_score IS NULL""",
        (MERGE_CONFIDENCE_THRESHOLD, MERGE_CONFIDENCE_THRESHOLD),
    )
    if rowcount > 0:
        logger.info("Auto-escalated %d consolidations to resolver (R)", rowcount)
    return rowcount


def _auto_reject() -> int:
    """Both confidence scores < reject threshold → set status = 'F'."""
    rowcount = execute_mutate(
        """UPDATE consolidations
           SET status = 'F', updated_at = now()
           WHERE status IN ('B1', 'B2')
             AND a_conf_score IS NOT NULL
             AND b_conf_score IS NOT NULL
             AND a_conf_score <= %s
             AND b_conf_score <= %s""",
        (REJECT_CONFIDENCE_THRESHOLD, REJECT_CONFIDENCE_THRESHOLD),
    )
    if rowcount > 0:
        logger.info("Auto-rejected %d consolidations (F)", rowcount)
    return rowcount
