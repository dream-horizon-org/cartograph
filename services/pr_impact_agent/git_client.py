"""Shallow-clone a PR HEAD into a temp dir for the LLM to read.

Phase 4: instead of relying on the GitHub patch hunk (3-line context), we
let Claude open the actual files. Generic across languages — Claude reads
Python/Go/Java/TS/etc natively, no per-language plumbing on our side.

Caller is responsible for cleanup via cleanup_workspace().
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)


_DEFAULT_TIMEOUT = int(os.getenv("PR_IMPACT_GIT_TIMEOUT", "180"))
_WORKSPACE_ROOT = os.getenv("PR_IMPACT_WORKSPACE_ROOT", "/tmp/pr_workspaces")


class GitCloneError(RuntimeError):
    pass


def _ensure_root() -> None:
    os.makedirs(_WORKSPACE_ROOT, exist_ok=True)


def shallow_clone(head_repo_full_name: str,
                  head_sha: str,
                  head_ref: str | None = None,
                  timeout: int | None = None) -> str:
    """Shallow-clone the PR's HEAD repo at head_sha into a fresh temp dir.

    `head_repo_full_name` must be the *fork's* full_name when the PR is
    from a fork (i.e. pr.head.repo.full_name from the GitHub API), not the
    base repo.

    Returns the absolute path to the cloned workspace. Raises GitCloneError.
    Caller must call cleanup_workspace(path) when done."""
    pat = os.getenv("GITHUB_PAT")
    if not pat:
        raise GitCloneError("GITHUB_PAT env var is not set")

    _ensure_root()

    safe_name = head_repo_full_name.replace("/", "_")
    tmpdir = tempfile.mkdtemp(
        prefix=f"{safe_name}_{head_sha[:8]}_",
        dir=_WORKSPACE_ROOT,
    )

    # PAT in URL is visible briefly to `ps` during clone — acceptable
    # tradeoff for local-dev simplicity. Production-grade hardening would
    # use a credential helper or git config write.
    clone_url = f"https://x-access-token:{pat}@github.com/{head_repo_full_name}.git"

    try:
        cmd = ["git", "clone", "--depth", "1"]
        if head_ref:
            cmd.extend(["--branch", head_ref])
        cmd.extend([clone_url, tmpdir])

        logger.info(
            "git clone (depth=1) %s @ %s → %s",
            head_repo_full_name, head_ref or head_sha, tmpdir,
        )
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout or _DEFAULT_TIMEOUT,
        )
        if proc.returncode != 0:
            # Strip the URL out of stderr in case git echoed it
            err = proc.stderr.replace(clone_url, f"https://***@github.com/{head_repo_full_name}.git")
            raise GitCloneError(
                f"git clone failed (exit {proc.returncode}): {err[:400]}"
            )

        # Verify we landed on head_sha. If new commits were pushed between
        # our PR-meta fetch and the clone, we'll be ahead. Try to fetch +
        # checkout the exact sha; warn if we can't.
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmpdir, capture_output=True, text=True, timeout=10,
        )
        actual_sha = rev.stdout.strip()
        if actual_sha != head_sha:
            logger.warning(
                "clone landed at %s but PR head_sha is %s — attempting fetch+checkout",
                actual_sha[:8], head_sha[:8],
            )
            fetch = subprocess.run(
                ["git", "fetch", "--depth", "1", "origin", head_sha],
                cwd=tmpdir, capture_output=True, text=True, timeout=60,
            )
            if fetch.returncode == 0:
                subprocess.run(
                    ["git", "checkout", head_sha],
                    cwd=tmpdir, capture_output=True, text=True, timeout=15,
                )
            else:
                logger.warning(
                    "couldn't fetch exact sha %s; analysis will run against %s "
                    "(diff is still exact since it came from the API)",
                    head_sha[:8], actual_sha[:8],
                )
        return tmpdir

    except subprocess.TimeoutExpired as e:
        cleanup_workspace(tmpdir)
        raise GitCloneError(
            f"git clone timed out after {timeout or _DEFAULT_TIMEOUT}s"
        ) from e
    except GitCloneError:
        cleanup_workspace(tmpdir)
        raise
    except Exception as e:
        cleanup_workspace(tmpdir)
        raise GitCloneError(f"unexpected git error: {e}") from e


def cleanup_workspace(path: str) -> None:
    """Remove a cloned workspace. Idempotent / safe if path missing."""
    if not path or not os.path.exists(path):
        return
    shutil.rmtree(path, ignore_errors=True)
    logger.info("cleaned up workspace %s", path)
