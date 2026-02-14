"""Generate commit messages for staged changes via Claude.

Provides the backend for the ``gitre label`` command.  Reuses the core
Claude SDK integration from :mod:`gitre.generator` — prompt building,
JSON extraction, and response parsing — with a simpler prompt tailored
to staged diffs rather than historical commits.
"""

from __future__ import annotations

from datetime import UTC, datetime

from gitre.analyzer import _run_git
from gitre.generator import (
    _SINGLE_OUTPUT_SCHEMA,
    _call_claude,
    _extract_json,
    _parse_single_response,
)
from gitre.models import CommitInfo, GeneratedMessage

# Maximum diff size to send to Claude (characters).
_MAX_DIFF_CHARS = 200_000


def get_staged_diff(repo_path: str) -> tuple[str, str]:
    """Return ``(diff_stat, diff_patch)`` for currently staged changes.

    Uses ``git diff --cached`` to inspect the staging area.
    Returns empty strings when nothing is staged.
    """
    stat_result = _run_git(["diff", "--cached", "--stat"], repo_path)
    patch_result = _run_git(["diff", "--cached", "--patch"], repo_path)
    return stat_result.stdout.strip(), patch_result.stdout


def _build_label_prompt(diff_stat: str, diff_patch: str) -> str:
    """Build a Claude prompt for analysing staged changes.

    Simplified version of :func:`generator._build_prompt` — no commit
    metadata (hash, author, date) since the changes haven't been
    committed yet.
    """
    if len(diff_patch) > _MAX_DIFF_CHARS:
        diff_patch = (
            diff_patch[:_MAX_DIFF_CHARS]
            + "\n\n[... diff truncated for size ...]"
        )

    return (
        "Analyze the following staged git changes and generate:\n"
        "1. A proper commit message (imperative mood, subject <72 chars, optional body)\n"
        "2. A changelog category (Added/Changed/Fixed/Removed/Deprecated/Security)\n"
        "3. A changelog entry (1-2 sentences)\n"
        "\n"
        "## Diff Statistics\n"
        f"{diff_stat}\n"
        "\n"
        "## Diff\n"
        f"{diff_patch}\n"
        "\n"
        "Respond with ONLY a JSON object:\n"
        "{\n"
        '    "subject": "imperative mood commit message, max 72 chars",\n'
        '    "body": "optional extended description or null",\n'
        '    "changelog_category": "Added|Changed|Fixed|Removed|Deprecated|Security",\n'
        '    "changelog_entry": "human-readable changelog entry"\n'
        "}"
    )


async def generate_label(
    repo_path: str,
    model: str = "opus",
) -> GeneratedMessage:
    """Generate a commit message for the currently staged changes.

    Orchestrates: staged diff extraction, prompt building, Claude SDK
    call, JSON extraction, and response parsing.  Reuses all low-level
    helpers from :mod:`gitre.generator`.

    Parameters
    ----------
    repo_path:
        Path to the git repository.
    model:
        Claude model to use.

    Returns
    -------
    GeneratedMessage
        Structured commit message with subject, optional body,
        changelog category, and changelog entry.

    Raises
    ------
    RuntimeError
        If there are no staged changes or Claude returns unparseable output.
    """
    diff_stat, diff_patch = get_staged_diff(repo_path)

    if not diff_patch.strip():
        raise RuntimeError("No staged changes to label.")

    prompt = _build_label_prompt(diff_stat, diff_patch)
    text, _, _ = await _call_claude(prompt, repo_path, model, _SINGLE_OUTPUT_SCHEMA)

    if not text.strip():
        raise RuntimeError("Empty response from Claude for staged changes.")

    raw = _extract_json(text)
    if isinstance(raw, list):
        if not raw:
            raise RuntimeError("Empty JSON array from Claude for staged changes.")
        raw = raw[0]

    # Create a placeholder CommitInfo for _parse_single_response
    placeholder = CommitInfo(
        hash="staged",
        short_hash="staged",
        author="",
        date=datetime.now(tz=UTC),
        original_message="[staged]",
        diff_stat=diff_stat,
        diff_patch=diff_patch,
        files_changed=0,
        insertions=0,
        deletions=0,
    )

    return _parse_single_response(raw, placeholder)
