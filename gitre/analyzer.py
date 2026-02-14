"""Walk git history and extract diffs using subprocess.

All git interaction uses subprocess.run — no GitPython dependency.
"""

from __future__ import annotations

import logging
import re
import subprocess
from datetime import UTC, datetime

from gitre.models import CommitInfo

logger = logging.getLogger(__name__)

# SHA of the git empty tree — used to diff against the root commit.
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Separator unlikely to appear in commit messages.
_FIELD_SEP = "---GITRE_SEP---"

# git log format: hash, short_hash, author, ISO date, subject%n%nbody
_LOG_FORMAT = f"%H{_FIELD_SEP}%h{_FIELD_SEP}%an{_FIELD_SEP}%aI{_FIELD_SEP}%B"

# Record separator between commits (must not clash with commit content).
_RECORD_SEP = "---GITRE_RECORD---"


def _run_git(
    args: list[str],
    cwd: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the CompletedProcess result.

    All output is decoded as UTF-8 with ``errors='replace'`` so binary
    file names or non-UTF-8 content never cause a crash.
    """
    cmd = ["git"] + args
    logger.debug("Running: %s (cwd=%s)", " ".join(cmd), cwd)
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        errors="replace",
        check=check,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_commits(
    repo_path: str,
    from_ref: str | None = None,
    to_ref: str | None = None,
) -> list[CommitInfo]:
    """Return an ordered list of commits in the given range.

    Parameters
    ----------
    repo_path:
        Path to the git repository.
    from_ref:
        Exclusive start ref (e.g. a tag or commit hash).  If *None*, the
        history goes back to the root.
    to_ref:
        Inclusive end ref.  Defaults to ``HEAD``.

    Returns
    -------
    list[CommitInfo]
        Commits in chronological order (oldest first).  Each commit has
        placeholder values for diff fields; call :func:`enrich_commit` to
        populate them.
    """
    # Build the revision range argument.
    if from_ref and to_ref:
        rev_range = f"{from_ref}..{to_ref}"
    elif from_ref:
        rev_range = f"{from_ref}..HEAD"
    elif to_ref:
        rev_range = to_ref
    else:
        rev_range = "HEAD"

    result = _run_git(
        [
            "log",
            "--reverse",
            f"--format={_RECORD_SEP}{_LOG_FORMAT}",
            rev_range,
        ],
        cwd=repo_path,
    )

    raw = result.stdout
    if not raw.strip():
        return []

    commits: list[CommitInfo] = []
    # Split on record separator and skip the empty first element.
    records = raw.split(_RECORD_SEP)
    for record in records:
        record = record.strip()
        if not record:
            continue

        parts = record.split(_FIELD_SEP, maxsplit=4)
        if len(parts) < 5:
            logger.warning("Skipping malformed log record: %r", record[:120])
            continue

        commit_hash, short_hash, author, date_str, message = parts
        commit_hash = commit_hash.strip()
        short_hash = short_hash.strip()
        author = author.strip()
        message = message.strip()

        # Parse the ISO-8601 date produced by %aI.
        commit_date = _parse_git_date(date_str.strip())

        # Detect version tags pointing at this commit.
        tags = _get_tags_for_commit(repo_path, commit_hash)

        commits.append(
            CommitInfo(
                hash=commit_hash,
                short_hash=short_hash,
                author=author,
                date=commit_date,
                original_message=message,
                diff_stat="",
                diff_patch="",
                files_changed=0,
                insertions=0,
                deletions=0,
                tags=tags,
            )
        )

    return commits


def get_diff(
    repo_path: str,
    commit_hash: str,
) -> tuple[str, str]:
    """Return ``(diff_stat, diff_patch)`` for a single commit.

    * For a **root commit** (no parent) the diff is computed against the
      git empty tree.
    * For a **merge commit** (more than one parent) the diff is empty and
      a note is returned instead.
    * For a **regular commit** the diff is against the first parent.
    """
    # Determine parent count.
    parent_result = _run_git(
        ["rev-parse", f"{commit_hash}^@"],
        cwd=repo_path,
        check=False,
    )
    parents = [p for p in parent_result.stdout.strip().splitlines() if p.strip()]

    if len(parents) > 1:
        # Merge commit — intentionally skip the diff.
        note = f"[merge commit {commit_hash[:10]} — diff omitted]"
        return (note, note)

    # Base to diff against.
    base = parents[0].strip() if parents else _EMPTY_TREE_SHA

    # Stat
    stat_result = _run_git(
        ["diff", "--stat", base, commit_hash],
        cwd=repo_path,
    )
    diff_stat = stat_result.stdout.strip()

    # Patch
    patch_result = _run_git(
        ["diff", "--patch", base, commit_hash],
        cwd=repo_path,
    )
    diff_patch = patch_result.stdout

    return diff_stat, diff_patch


def truncate_diff(diff_patch: str, max_bytes: int = 50_000) -> str:
    """Truncate *diff_patch* if it exceeds *max_bytes*.

    The byte length is measured after encoding to UTF-8.  If truncation
    occurs the string ``[diff truncated]`` is appended.
    """
    encoded = diff_patch.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return diff_patch

    # Truncate at byte boundary, then decode back (replace avoids mid-char errors).
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return truncated + "\n[diff truncated]"


def enrich_commit(
    repo_path: str,
    commit: CommitInfo,
) -> CommitInfo:
    """Fill in diff and stat fields for *commit*.

    Returns a **new** ``CommitInfo`` (the model is frozen/immutable).
    """
    diff_stat, diff_patch = get_diff(repo_path, commit.hash)

    # Parse --numstat for structured counts.
    files_changed, insertions, deletions = _parse_numstat(repo_path, commit.hash)

    return commit.model_copy(
        update={
            "diff_stat": diff_stat,
            "diff_patch": diff_patch,
            "files_changed": files_changed,
            "insertions": insertions,
            "deletions": deletions,
        },
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_git_date(date_str: str) -> datetime:
    """Parse an ISO-8601 date string emitted by ``git log --format=%aI``."""
    # Python 3.11+ handles the colon in the timezone offset via fromisoformat.
    try:
        return datetime.fromisoformat(date_str)
    except ValueError:
        # Fallback: strip the colon from +HH:MM → +HHMM for older Pythons.
        cleaned = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", date_str)
        try:
            return datetime.strptime(cleaned, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            logger.warning("Could not parse date %r; using UTC now", date_str)
            return datetime.now(tz=UTC)


def _get_tags_for_commit(repo_path: str, commit_hash: str) -> list[str]:
    """Return tags that point at *commit_hash*."""
    result = _run_git(
        ["tag", "--points-at", commit_hash],
        cwd=repo_path,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    return [t.strip() for t in result.stdout.strip().splitlines() if t.strip()]


def _parse_numstat(
    repo_path: str,
    commit_hash: str,
) -> tuple[int, int, int]:
    """Run ``git diff --numstat`` and return (files_changed, insertions, deletions).

    Binary files report ``-`` for insertions/deletions — those are counted as
    0 for the numeric totals but still count as a changed file.
    """
    # Determine parent(s).
    parent_result = _run_git(
        ["rev-parse", f"{commit_hash}^@"],
        cwd=repo_path,
        check=False,
    )
    parents = [p for p in parent_result.stdout.strip().splitlines() if p.strip()]

    if len(parents) > 1:
        # Merge commit — no numstat.
        return 0, 0, 0

    base = parents[0].strip() if parents else _EMPTY_TREE_SHA

    numstat = _run_git(
        ["diff", "--numstat", base, commit_hash],
        cwd=repo_path,
    )

    files_changed = 0
    insertions = 0
    deletions = 0

    for line in numstat.stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", maxsplit=2)
        if len(parts) < 3:
            continue
        add_str, del_str, _filename = parts
        files_changed += 1
        # Binary files show '-' for add/del counts.
        if add_str != "-":
            insertions += int(add_str)
        if del_str != "-":
            deletions += int(del_str)

    return files_changed, insertions, deletions
