"""Output formatting for changelogs and commit messages.

Provides functions to render GeneratedMessage and CommitInfo data into
human-readable changelog (Keep a Changelog) and commit-message review formats.
"""

from __future__ import annotations

from collections import defaultdict

from gitre.models import CommitInfo, GeneratedMessage

# Ordered categories following Keep a Changelog convention (most impactful first).
_CATEGORY_ORDER: list[str] = [
    "Added",
    "Changed",
    "Deprecated",
    "Removed",
    "Fixed",
    "Security",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _group_messages_by_version(
    messages: list[GeneratedMessage],
    tags: dict[str, str],
) -> dict[str, list[GeneratedMessage]]:
    """Group messages into version sections.

    *tags* maps commit hashes to version strings (e.g. ``{"abc123": "v1.0.0"}``).

    Messages whose hash appears in *tags* are placed under that tag's version
    heading.  Messages whose hash does **not** appear in *tags* are placed
    under the ``"Unreleased"`` heading.

    Returns a dict keyed by version label (``"Unreleased"`` or the tag value)
    in insertion order matching the original message list order so that the
    caller can iterate newest-first.
    """
    # Build a hash -> tag lookup for O(1) membership checks.
    hash_to_tag: dict[str, str] = {}
    for commit_hash, tag_name in tags.items():
        hash_to_tag[commit_hash] = tag_name

    # Preserve ordering: walk messages (assumed newest-first) and bucket them.
    ordered_versions: list[str] = []
    groups: dict[str, list[GeneratedMessage]] = defaultdict(list)

    for msg in messages:
        version = hash_to_tag.get(msg.hash, "Unreleased")
        if version not in groups:
            ordered_versions.append(version)
        groups[version].append(msg)

    # Return an ordered dict.
    return {v: groups[v] for v in ordered_versions}


def _render_category_block(
    entries: list[GeneratedMessage],
) -> str:
    """Render categorised bullet entries for a single version section."""
    by_category: dict[str, list[str]] = defaultdict(list)
    for msg in entries:
        by_category[msg.changelog_category].append(msg.changelog_entry)

    lines: list[str] = []
    for cat in _CATEGORY_ORDER:
        if cat not in by_category:
            continue
        lines.append(f"### {cat}")
        for entry in by_category[cat]:
            lines.append(f"- {entry}")
        lines.append("")  # blank line after each category block

    return "\n".join(lines)


def _format_version_heading(
    version: str,
    entries: list[GeneratedMessage],
    tags: dict[str, str],
) -> str:
    """Return the ``## [Version]`` heading line for a version section.

    For tagged versions the date is derived from the first message in the
    group (assumed to be the tagged commit).  For ``Unreleased`` no date is
    shown.
    """
    if version == "Unreleased":
        return "## [Unreleased]"

    # Try to find the date from the entries — not available on GeneratedMessage,
    # so we simply use the version label without a date when we lack commit info.
    return f"## [{version}]"


def _build_comparison_links(
    ordered_versions: list[str],
    repo_url: str,
) -> str:
    """Build comparison link definitions for the bottom of the changelog.

    Each version gets a link comparing it to the previous version.  The
    ``Unreleased`` section links from ``HEAD`` to the most recent tag.
    """
    repo_url = repo_url.rstrip("/")
    lines: list[str] = []

    for i, version in enumerate(ordered_versions):
        if version == "Unreleased":
            # Link from latest tag to HEAD.
            if i + 1 < len(ordered_versions):
                prev = ordered_versions[i + 1]
                lines.append(f"[Unreleased]: {repo_url}/compare/{prev}...HEAD")
            else:
                # No previous tag — link to full commit log.
                lines.append(f"[Unreleased]: {repo_url}/commits/HEAD")
        else:
            if i + 1 < len(ordered_versions):
                prev = ordered_versions[i + 1]
                if prev == "Unreleased":
                    # Skip — shouldn't happen with well-ordered data.
                    continue
                lines.append(f"[{version}]: {repo_url}/compare/{prev}...{version}")
            else:
                # First ever version — link to the tag itself.
                lines.append(f"[{version}]: {repo_url}/releases/tag/{version}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def format_changelog(
    messages: list[GeneratedMessage],
    tags: dict[str, str],
    repo_url: str | None = None,
    format_style: str = "keepachangelog",
) -> str:
    """Generate a changelog in *Keep a Changelog* format.

    Parameters
    ----------
    messages:
        Generated messages to include, assumed newest-first.
    tags:
        Mapping of commit hashes to version tag strings.
    repo_url:
        Optional repository URL used to produce comparison links at the
        bottom of the changelog.
    format_style:
        Currently only ``"keepachangelog"`` is supported.  Included for
        future extensibility.

    Returns
    -------
    str
        The rendered changelog text.
    """
    if not messages:
        return (
            "# Changelog\n\n"
            "All notable changes to this project will be documented in this file.\n\n"
            "## [Unreleased]\n\nNo changes yet.\n"
        )

    parts: list[str] = [
        "# Changelog",
        "",
        "All notable changes to this project will be documented in this file.",
        "",
    ]

    version_groups = _group_messages_by_version(messages, tags)
    ordered_versions = list(version_groups.keys())

    for version in ordered_versions:
        entries = version_groups[version]
        heading = _format_version_heading(version, entries, tags)
        parts.append(heading)
        parts.append("")
        category_block = _render_category_block(entries)
        if category_block:
            parts.append(category_block)

    # Comparison links
    if repo_url and ordered_versions:
        links = _build_comparison_links(ordered_versions, repo_url)
        if links:
            parts.append(links)
            parts.append("")

    return "\n".join(parts)


def format_messages(
    messages: list[GeneratedMessage],
    commits: list[CommitInfo] | None = None,
) -> str:
    """Format the proposed commit-message review display.

    Shows each commit with its hash, date, original message, and proposed
    message side-by-side so the user can review changes.

    Parameters
    ----------
    messages:
        Generated messages (one per commit).
    commits:
        Optional list of original ``CommitInfo`` objects.  When provided the
        original message and date are sourced from here; otherwise only the
        generated side is shown.

    Returns
    -------
    str
        Formatted text block headed by
        ``=== Proposed Commit Messages ===``.
    """
    if not messages:
        return "=== Proposed Commit Messages ===\n\nNo messages to display.\n"

    # Build a hash -> CommitInfo lookup for efficient pairing.
    commit_map: dict[str, CommitInfo] = {}
    if commits:
        for c in commits:
            commit_map[c.hash] = c

    lines: list[str] = ["=== Proposed Commit Messages ===", ""]

    for idx, msg in enumerate(messages):
        commit = commit_map.get(msg.hash)

        # Header with hash
        lines.append(f"--- Commit {idx + 1}: {msg.short_hash} ---")

        if commit:
            lines.append(f"Date:     {commit.date:%Y-%m-%d %H:%M:%S}")
            lines.append(f"Author:   {commit.author}")
            lines.append("")
            lines.append(f"Original: {commit.original_message}")
        else:
            lines.append("")
            lines.append(f"Hash:     {msg.hash}")

        # Proposed message
        proposed = msg.subject
        if msg.body:
            proposed = f"{msg.subject}\n\n{msg.body}"
        lines.append(f"Proposed: {proposed}")

        # Changelog hint
        lines.append(f"Category: [{msg.changelog_category}] {msg.changelog_entry}")
        lines.append("")

    return "\n".join(lines)


def format_both(
    messages: list[GeneratedMessage],
    commits: list[CommitInfo],
    tags: dict[str, str],
    repo_url: str | None = None,
) -> str:
    """Combine commit-message review and changelog into a single output.

    The commit-message review is printed first, followed by a separator and
    the full changelog.

    Parameters
    ----------
    messages:
        Generated messages.
    commits:
        Original commit information.
    tags:
        Hash-to-tag mapping.
    repo_url:
        Optional repository URL for comparison links.

    Returns
    -------
    str
        Combined formatted output.
    """
    msg_section = format_messages(messages, commits)
    changelog_section = format_changelog(messages, tags, repo_url)

    separator = "=" * 60

    return f"{msg_section}\n{separator}\n\n{changelog_section}"
