"""Claude Agent SDK integration for generating commit messages and changelog entries.

Uses the Claude Agent SDK to analyze git diffs and produce structured commit
messages and changelog entries. All SDK interactions follow the gotchas
documented in the project directive.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

from gitre.models import CommitInfo, GeneratedMessage

# --- Import guard: graceful error when SDK not installed ---
try:
    from claude_agent_sdk import ClaudeAgentOptions, query
    from claude_agent_sdk.types import AssistantMessage, ResultMessage

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    # Provide stubs so the module can be imported without the SDK
    query = None  # type: ignore[assignment]
    ClaudeAgentOptions = None  # type: ignore[assignment,misc]
    AssistantMessage = None  # type: ignore[assignment,misc]
    ResultMessage = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# Expected keys that MUST appear in a valid single-commit response
_EXPECTED_SINGLE_KEYS = {"subject", "changelog_category"}
# Expected shape regex for single-commit JSON
_SINGLE_JSON_RE = re.compile(r'\{\s*"subject"', re.DOTALL)

# System prompt for the Claude agent
_SYSTEM_PROMPT = (
    "You are a git commit message analyst. Your job is to analyze git diffs "
    "and generate clear, conventional commit messages in imperative mood "
    "(e.g. 'Add feature', not 'Added feature') and changelog entries. "
    "Always respond with ONLY valid JSON — no prose, no markdown fences, "
    "no explanation."
)

# Maximum diff size to send to Claude (characters). Diffs larger than this
# are truncated to avoid blowing up the context window.
_MAX_DIFF_CHARS = 200_000


@dataclass(frozen=True)
class BatchResult:
    """Wrapper for batch generation results, including token/cost accounting."""

    messages: list[GeneratedMessage]
    total_tokens: int = 0
    total_cost: float = 0.0


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def _build_prompt(commit: CommitInfo) -> str:
    """Build the analysis prompt for a single commit.

    Includes commit metadata, diff statistics, and the full diff patch
    (truncated if too large) in the format specified by the directive.
    """
    diff_patch = commit.diff_patch
    if len(diff_patch) > _MAX_DIFF_CHARS:
        diff_patch = (
            diff_patch[:_MAX_DIFF_CHARS]
            + "\n\n[... diff truncated for size ...]"
        )

    tags_str = ", ".join(commit.tags) if commit.tags else "none"

    return (
        "Analyze the following git commit and generate:\n"
        "1. A proper commit message (imperative mood, subject <72 chars, optional body)\n"
        "2. A changelog category (Added/Changed/Fixed/Removed/Deprecated/Security)\n"
        "3. A changelog entry (1-2 sentences)\n"
        "\n"
        "## Commit Metadata\n"
        f"- Hash: {commit.short_hash}\n"
        f"- Author: {commit.author}\n"
        f"- Date: {commit.date}\n"
        f"- Original message: {commit.original_message}\n"
        f"- Files changed: {commit.files_changed} "
        f"({commit.insertions} insertions, {commit.deletions} deletions)\n"
        f"- Tags: {tags_str}\n"
        "\n"
        "## Diff Statistics\n"
        f"{commit.diff_stat}\n"
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


def _build_batch_prompt(commits: list[CommitInfo]) -> str:
    """Build a prompt for analysing multiple commits at once.

    Instructs Claude to return a JSON **array** with one object per commit,
    in the same order as the input commits.
    """
    parts: list[str] = [
        "Analyze the following git commits and generate for EACH commit:\n"
        "1. A proper commit message (imperative mood, subject <72 chars, optional body)\n"
        "2. A changelog category (Added/Changed/Fixed/Removed/Deprecated/Security)\n"
        "3. A changelog entry (1-2 sentences)\n"
        "\n"
        "Return a JSON **array** with one object per commit, in the SAME ORDER "
        "as they appear below. Each object must have the keys: "
        '"subject", "body", "changelog_category", "changelog_entry".\n'
    ]

    for idx, commit in enumerate(commits, start=1):
        diff_patch = commit.diff_patch
        if len(diff_patch) > _MAX_DIFF_CHARS:
            diff_patch = (
                diff_patch[:_MAX_DIFF_CHARS]
                + "\n\n[... diff truncated for size ...]"
            )

        tags_str = ", ".join(commit.tags) if commit.tags else "none"

        parts.append(
            f"\n---\n## Commit {idx} of {len(commits)}\n"
            f"- Hash: {commit.short_hash}\n"
            f"- Author: {commit.author}\n"
            f"- Date: {commit.date}\n"
            f"- Original message: {commit.original_message}\n"
            f"- Files changed: {commit.files_changed} "
            f"({commit.insertions} insertions, {commit.deletions} deletions)\n"
            f"- Tags: {tags_str}\n"
            f"\n### Diff Statistics\n{commit.diff_stat}\n"
            f"\n### Diff\n{diff_patch}\n"
        )

    parts.append(
        "\n---\n"
        "Respond with ONLY a JSON array (one object per commit, same order):\n"
        "[\n"
        "  {\n"
        '    "subject": "...",\n'
        '    "body": "... or null",\n'
        '    "changelog_category": "Added|Changed|Fixed|Removed|Deprecated|Security",\n'
        '    "changelog_entry": "..."\n'
        "  },\n"
        "  ...\n"
        "]"
    )

    return "".join(parts)


# ---------------------------------------------------------------------------
# JSON extraction (multi-strategy)
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict | list:
    """Extract JSON from Claude's response using multiple fallback strategies.

    Strategy 1: Direct ``json.loads`` on the full text.
    Strategy 2: Extract from markdown code fences — try ALL fences, not just
                the first, using ``re.finditer``.
    Strategy 3: Find the first ``[`` or ``{`` and attempt to parse from there,
                with key validation. Arrays are tried BEFORE objects so that
                batch responses (JSON arrays) embedded in prose are parsed as
                full arrays rather than extracting just the first object.
    Strategy 4: Regex for the expected single-object JSON shape
                (``{"subject"...``). Tried last to avoid extracting a single
                object from inside a JSON array.

    Raises ``ValueError`` if no valid JSON can be extracted.
    """
    text = text.strip()

    # Strategy 1: direct parse
    try:
        result = json.loads(text)
        if isinstance(result, (dict, list)):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: markdown code fences (try ALL fences)
    fence_pattern = re.compile(
        r"```(?:json)?\s*\n?(.*?)```", re.DOTALL
    )
    for match in fence_pattern.finditer(text):
        candidate = match.group(1).strip()
        try:
            result = json.loads(candidate)
            if isinstance(result, (dict, list)):
                return result
        except (json.JSONDecodeError, ValueError):
            continue

    # Strategy 3: find first '[' or '{' with key validation
    # IMPORTANT: try '[' (arrays) BEFORE '{' (objects) so that batch
    # responses like 'Here are the results: [{"subject":...}, ...]'
    # are parsed as a full array rather than extracting just the first
    # object via the single-object regex.
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        idx = text.find(start_char)
        if idx == -1:
            continue

        candidate = text[idx:]
        # Try parsing the remainder directly
        try:
            result = json.loads(candidate)
            if isinstance(result, (dict, list)):
                if _validate_json_keys(result):
                    return result
        except (json.JSONDecodeError, ValueError):
            # Try to find the matching closing character via depth tracking
            depth = 0
            for i, ch in enumerate(candidate):
                if ch == start_char:
                    depth += 1
                elif ch == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            result = json.loads(candidate[: i + 1])
                            if isinstance(result, (dict, list)):
                                if _validate_json_keys(result):
                                    return result
                        except (json.JSONDecodeError, ValueError):
                            pass
                        break

    # Strategy 4: regex for expected single-object shape ({"subject"...)
    # This is tried AFTER the array/object scan above so that batch
    # responses wrapped in prose don't lose all but the first element.
    shape_match = _SINGLE_JSON_RE.search(text)
    if shape_match:
        start = shape_match.start()
        candidate = text[start:]
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            # Try to find matching closing brace
            depth = 0
            for i, ch in enumerate(candidate):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            result = json.loads(candidate[: i + 1])
                            if isinstance(result, dict):
                                return result
                        except (json.JSONDecodeError, ValueError):
                            break

    raise ValueError(
        f"Could not extract valid JSON from Claude response: {text[:200]}..."
        if len(text) > 200
        else f"Could not extract valid JSON from Claude response: {text}"
    )


def _validate_json_keys(data: dict | list) -> bool:
    """Check that the extracted JSON contains expected keys.

    Prevents extracting random JSON objects from prose. For a single
    commit response we expect ``"subject"`` and ``"changelog_category"``.
    For a batch response (list), we check the first element.
    """
    if isinstance(data, list):
        if not data:
            return False
        # Validate first element
        if isinstance(data[0], dict):
            return _EXPECTED_SINGLE_KEYS.issubset(data[0].keys())
        return False
    if isinstance(data, dict):
        return _EXPECTED_SINGLE_KEYS.issubset(data.keys())
    return False


# ---------------------------------------------------------------------------
# Output format schema for structured JSON responses
# ---------------------------------------------------------------------------

_SINGLE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {
            "type": "string",
            "description": "Imperative mood commit message, max 72 chars",
        },
        "body": {
            "type": ["string", "null"],
            "description": "Optional extended description",
        },
        "changelog_category": {
            "type": "string",
            "enum": [
                "Added",
                "Changed",
                "Fixed",
                "Removed",
                "Deprecated",
                "Security",
            ],
            "description": "Keep a Changelog category",
        },
        "changelog_entry": {
            "type": "string",
            "description": "Human-readable changelog entry",
        },
    },
    "required": ["subject", "changelog_category", "changelog_entry"],
}

_BATCH_OUTPUT_SCHEMA = {
    "type": "array",
    "items": _SINGLE_OUTPUT_SCHEMA,
}


# ---------------------------------------------------------------------------
# Core generation functions
# ---------------------------------------------------------------------------


def _ensure_sdk() -> None:
    """Raise a clear error if the Claude Agent SDK is not installed."""
    if not SDK_AVAILABLE:
        raise RuntimeError(
            "claude-agent-sdk is not installed. "
            "Install it with: pip install claude-agent-sdk>=0.1.30"
        )


def _build_options(
    cwd: str,
    model: str,
    output_schema: dict,
) -> ClaudeAgentOptions:  # type: ignore[valid-type]
    """Build ``ClaudeAgentOptions`` with all required SDK settings.

    Follows every SDK gotcha from the directive:
    - ``bypassPermissions`` with ``allowed_tools=["Read"]``
    - Stripped ``ANTHROPIC_API_KEY`` from env
    - 10 MB ``max_buffer_size``
    - Low ``max_turns`` (3)
    - ``output_format`` for JSON schema
    """
    return ClaudeAgentOptions(
        system_prompt=_SYSTEM_PROMPT,
        allowed_tools=["Read"],
        permission_mode="bypassPermissions",
        cwd=cwd,
        model=model,
        max_buffer_size=10 * 1024 * 1024,  # 10 MB
        max_turns=3,
        env={k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"},
        output_format=output_schema,
    )


async def _call_claude(
    prompt: str,
    cwd: str,
    model: str,
    output_schema: dict,
) -> tuple[str, int, float]:
    """Low-level wrapper around ``query()`` that returns (text, tokens, cost).

    Iterates over the async stream of messages, collecting text blocks from
    ``AssistantMessage`` events and cost/token info from ``ResultMessage``.
    """
    _ensure_sdk()

    options = _build_options(cwd, model, output_schema)

    output_parts: list[str] = []
    total_cost: float = 0.0
    total_tokens: int = 0

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    output_parts.append(block.text)
        elif isinstance(message, ResultMessage):
            total_cost = getattr(message, "total_cost_usd", 0.0) or 0.0
            usage = getattr(message, "usage", None)
            if usage and isinstance(usage, dict):
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                total_tokens = input_tokens + output_tokens

    text = "\n".join(output_parts)
    return text, total_tokens, total_cost


def _parse_single_response(
    raw: dict,
    commit: CommitInfo,
) -> GeneratedMessage:
    """Convert a raw JSON dict into a ``GeneratedMessage``, filling in
    commit hash fields from the source ``CommitInfo``.
    """
    subject = str(raw.get("subject", ""))
    # Truncate subject if it exceeds 72 chars (defensive)
    if len(subject) > 72:
        subject = subject[:69] + "..."

    body = raw.get("body")
    if body is not None:
        body = str(body)

    changelog_category = str(raw.get("changelog_category", "Changed"))
    changelog_entry = str(raw.get("changelog_entry", ""))

    return GeneratedMessage(
        hash=commit.hash,
        short_hash=commit.short_hash,
        subject=subject,
        body=body,
        changelog_category=changelog_category,
        changelog_entry=changelog_entry,
    )


async def generate_message(
    commit: CommitInfo,
    cwd: str,
    model: str = "sonnet",
) -> GeneratedMessage:
    """Generate a commit message and changelog entry for a single commit.

    Calls Claude via ``query()`` with ``ClaudeAgentOptions`` configured per
    the SDK gotchas. Collects text from ``AssistantMessage`` blocks and
    cost/tokens from ``ResultMessage``. Parses the response into a
    ``GeneratedMessage``.

    Parameters
    ----------
    commit:
        The commit to analyse.
    cwd:
        Working directory for the Claude agent (typically the repo root).
    model:
        Claude model to use (default ``"sonnet"``).

    Returns
    -------
    GeneratedMessage
        Structured commit message and changelog entry.

    Raises
    ------
    RuntimeError
        If the SDK is not installed or Claude returns unparseable output.
    """
    prompt = _build_prompt(commit)
    text, total_tokens, total_cost = await _call_claude(
        prompt, cwd, model, _SINGLE_OUTPUT_SCHEMA
    )

    logger.debug(
        "Claude response for %s: tokens=%d cost=%.4f",
        commit.short_hash,
        total_tokens,
        total_cost,
    )

    if not text.strip():
        raise RuntimeError(
            f"Empty response from Claude for commit {commit.short_hash}"
        )

    raw = _extract_json(text)
    if isinstance(raw, list):
        if not raw:
            raise RuntimeError(
                f"Empty JSON array from Claude for commit {commit.short_hash}"
            )
        raw = raw[0]

    return _parse_single_response(raw, commit)


async def generate_messages_batch(
    commits: list[CommitInfo],
    cwd: str,
    model: str = "sonnet",
) -> BatchResult:
    """Generate commit messages for multiple commits in a single Claude call.

    Sends all commits in one prompt, instructing Claude to return a JSON
    array. Falls back to individual calls if the batch response cannot be
    parsed.

    Parameters
    ----------
    commits:
        List of commits to analyse.
    cwd:
        Working directory for the Claude agent.
    model:
        Claude model to use.

    Returns
    -------
    BatchResult
        Contains the list of ``GeneratedMessage`` objects plus aggregate
        ``total_tokens`` and ``total_cost``.
    """
    if not commits:
        return BatchResult(messages=[])

    # Single commit — delegate to the simpler function
    if len(commits) == 1:
        msg = await generate_message(commits[0], cwd, model)
        return BatchResult(messages=[msg], total_tokens=0, total_cost=0.0)

    prompt = _build_batch_prompt(commits)
    text, total_tokens, total_cost = await _call_claude(
        prompt, cwd, model, _BATCH_OUTPUT_SCHEMA
    )

    logger.debug(
        "Batch Claude response: tokens=%d cost=%.4f",
        total_tokens,
        total_cost,
    )

    if not text.strip():
        raise RuntimeError("Empty response from Claude for batch request")

    raw = _extract_json(text)

    # If Claude returned a single object instead of an array, wrap it
    if isinstance(raw, dict):
        raw = [raw]

    if not isinstance(raw, list):
        raise RuntimeError(
            f"Expected a JSON array from batch response, got {type(raw).__name__}"
        )

    messages: list[GeneratedMessage] = []
    for idx, commit in enumerate(commits):
        if idx < len(raw) and isinstance(raw[idx], dict):
            messages.append(_parse_single_response(raw[idx], commit))
        else:
            logger.warning(
                "Missing response for commit %s (index %d) in batch; "
                "falling back to individual call",
                commit.short_hash,
                idx,
            )
            # Fallback: call individually for missing entries
            msg = await generate_message(commit, cwd, model)
            messages.append(msg)

    return BatchResult(
        messages=messages,
        total_tokens=total_tokens,
        total_cost=total_cost,
    )
