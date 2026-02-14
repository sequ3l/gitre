"""Tests for gitre.formatter — output formatting for changelogs and commit messages.

Covers:
 1. format_changelog produces valid Keep a Changelog format with correct header.
 2. format_changelog groups entries by version tag correctly.
 3. format_changelog creates [Unreleased] section for commits after latest tag.
 4. format_changelog orders versions newest-first.
 5. format_changelog categorizes entries correctly (Added, Changed, Fixed, etc.).
 6. format_changelog handles no tags (groups by date).
 7. format_changelog adds comparison links when repo_url provided.
 8. format_messages shows original vs proposed with correct formatting.
 9. format_both combines messages and changelog.
10. Edge cases: single commit, no commits, all same category.
"""

from __future__ import annotations

from datetime import datetime

from gitre.formatter import format_both, format_changelog, format_messages
from gitre.models import CommitInfo, GeneratedMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_msg(
    *,
    hash: str = "abc1234567890",
    short_hash: str = "abc1234",
    subject: str = "Fix a bug",
    body: str | None = None,
    changelog_category: str = "Fixed",
    changelog_entry: str = "Fixed a critical bug",
) -> GeneratedMessage:
    return GeneratedMessage(
        hash=hash,
        short_hash=short_hash,
        subject=subject,
        body=body,
        changelog_category=changelog_category,
        changelog_entry=changelog_entry,
    )


def _make_commit(
    *,
    hash: str = "abc1234567890",
    short_hash: str = "abc1234",
    author: str = "Test User",
    date: datetime | None = None,
    original_message: str = "fix bug",
    diff_stat: str = "1 file changed",
    diff_patch: str = "",
    files_changed: int = 1,
    insertions: int = 5,
    deletions: int = 2,
    tags: list[str] | None = None,
) -> CommitInfo:
    return CommitInfo(
        hash=hash,
        short_hash=short_hash,
        author=author,
        date=date or datetime(2025, 1, 15, 10, 30, 0),
        original_message=original_message,
        diff_stat=diff_stat,
        diff_patch=diff_patch,
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions,
        tags=tags or [],
    )


# ===========================================================================
# 1. format_changelog produces valid Keep a Changelog format with correct header
# ===========================================================================


class TestFormatChangelogHeader:
    """format_changelog produces valid Keep a Changelog format with correct header."""

    def test_header_starts_with_changelog_heading(self) -> None:
        msgs = [_make_msg()]
        result = format_changelog(msgs, {})
        lines = result.split("\n")
        assert lines[0] == "# Changelog"

    def test_header_contains_preamble(self) -> None:
        msgs = [_make_msg()]
        result = format_changelog(msgs, {})
        assert "All notable changes to this project will be documented in this file." in result

    def test_header_present_when_empty(self) -> None:
        result = format_changelog([], {})
        assert result.startswith("# Changelog")
        assert "All notable changes to this project will be documented in this file." in result

    def test_version_sections_use_h2_headings(self) -> None:
        msgs = [_make_msg(hash="h1", short_hash="h1")]
        tags = {"h1": "v1.0.0"}
        result = format_changelog(msgs, tags)
        assert "## [v1.0.0]" in result

    def test_categories_use_h3_headings(self) -> None:
        msgs = [_make_msg()]
        result = format_changelog(msgs, {})
        assert "### Fixed" in result


# ===========================================================================
# 2. format_changelog groups entries by version tag correctly
# ===========================================================================


class TestFormatChangelogGroupsByTag:
    """format_changelog groups entries by version tag correctly."""

    def test_tagged_commits_grouped_under_version(self) -> None:
        msgs = [
            _make_msg(hash="h1", short_hash="h1", changelog_entry="v2 feature"),
            _make_msg(hash="h2", short_hash="h2", changelog_entry="v1 feature"),
        ]
        tags = {"h1": "v2.0.0", "h2": "v1.0.0"}
        result = format_changelog(msgs, tags)
        assert "## [v2.0.0]" in result
        assert "## [v1.0.0]" in result
        # v2 feature appears after v2 heading but before v1 heading
        v2_heading = result.index("## [v2.0.0]")
        v1_heading = result.index("## [v1.0.0]")
        v2_entry = result.index("v2 feature")
        v1_entry = result.index("v1 feature")
        assert v2_heading < v2_entry < v1_heading
        assert v1_heading < v1_entry

    def test_mixed_tagged_and_untagged(self) -> None:
        msgs = [
            _make_msg(hash="h1", short_hash="h1", changelog_entry="unreleased work"),
            _make_msg(hash="h2", short_hash="h2", changelog_entry="tagged work"),
        ]
        tags = {"h2": "v1.0.0"}
        result = format_changelog(msgs, tags)
        assert "## [Unreleased]" in result
        assert "## [v1.0.0]" in result

    def test_all_messages_tagged_no_unreleased(self) -> None:
        """No Unreleased section when every commit is tagged."""
        msgs = [_make_msg(hash="h1", short_hash="h1")]
        tags = {"h1": "v1.0.0"}
        result = format_changelog(msgs, tags)
        assert "Unreleased" not in result
        assert "## [v1.0.0]" in result

    def test_multiple_commits_under_same_tag(self) -> None:
        """Two messages sharing the same tag hash appear in the same section."""
        msgs = [
            _make_msg(hash="h1", short_hash="h1", changelog_entry="first entry"),
            _make_msg(hash="h1", short_hash="h1", changelog_entry="second entry"),
        ]
        tags = {"h1": "v1.0.0"}
        result = format_changelog(msgs, tags)
        # Both entries should appear
        assert "first entry" in result
        assert "second entry" in result
        # Only one v1.0.0 heading
        assert result.count("## [v1.0.0]") == 1


# ===========================================================================
# 3. format_changelog creates [Unreleased] section for commits after latest tag
# ===========================================================================


class TestFormatChangelogUnreleased:
    """format_changelog creates [Unreleased] section for commits after latest tag."""

    def test_untagged_commits_go_to_unreleased(self) -> None:
        msgs = [
            _make_msg(hash="h1", short_hash="h1", changelog_entry="new work"),
            _make_msg(hash="h2", short_hash="h2", changelog_entry="released work"),
        ]
        tags = {"h2": "v1.0.0"}
        result = format_changelog(msgs, tags)
        # Unreleased section exists
        assert "## [Unreleased]" in result
        # New work is in the Unreleased section (before v1.0.0)
        unreleased_pos = result.index("## [Unreleased]")
        v1_pos = result.index("## [v1.0.0]")
        new_work_pos = result.index("new work")
        assert unreleased_pos < new_work_pos < v1_pos

    def test_multiple_untagged_commits_in_unreleased(self) -> None:
        msgs = [
            _make_msg(hash="h1", short_hash="h1", changelog_entry="work A"),
            _make_msg(hash="h2", short_hash="h2", changelog_entry="work B"),
            _make_msg(hash="h3", short_hash="h3", changelog_entry="released"),
        ]
        tags = {"h3": "v1.0.0"}
        result = format_changelog(msgs, tags)
        assert "## [Unreleased]" in result
        assert "work A" in result
        assert "work B" in result
        # Both unreleased entries should appear before v1 heading
        v1_pos = result.index("## [v1.0.0]")
        assert result.index("work A") < v1_pos
        assert result.index("work B") < v1_pos

    def test_unreleased_heading_format(self) -> None:
        msgs = [_make_msg()]
        result = format_changelog(msgs, {})
        assert "## [Unreleased]" in result

    def test_empty_messages_shows_unreleased(self) -> None:
        result = format_changelog([], {})
        assert "## [Unreleased]" in result
        assert "No changes yet." in result


# ===========================================================================
# 4. format_changelog orders versions newest-first
# ===========================================================================


class TestFormatChangelogVersionOrder:
    """format_changelog orders versions newest-first."""

    def test_unreleased_before_all_versions(self) -> None:
        msgs = [
            _make_msg(hash="h1", short_hash="h1", changelog_entry="unrel"),
            _make_msg(hash="h2", short_hash="h2", changelog_entry="v2"),
            _make_msg(hash="h3", short_hash="h3", changelog_entry="v1"),
        ]
        tags = {"h2": "v2.0.0", "h3": "v1.0.0"}
        result = format_changelog(msgs, tags)
        unreleased_pos = result.index("[Unreleased]")
        v2_pos = result.index("[v2.0.0]")
        v1_pos = result.index("[v1.0.0]")
        assert unreleased_pos < v2_pos < v1_pos

    def test_newest_version_first(self) -> None:
        """Messages given newest-first result in newest version heading first."""
        msgs = [
            _make_msg(hash="h1", short_hash="h1", changelog_entry="latest"),
            _make_msg(hash="h2", short_hash="h2", changelog_entry="middle"),
            _make_msg(hash="h3", short_hash="h3", changelog_entry="oldest"),
        ]
        tags = {"h1": "v3.0.0", "h2": "v2.0.0", "h3": "v1.0.0"}
        result = format_changelog(msgs, tags)
        v3_pos = result.index("[v3.0.0]")
        v2_pos = result.index("[v2.0.0]")
        v1_pos = result.index("[v1.0.0]")
        assert v3_pos < v2_pos < v1_pos

    def test_two_versions_order(self) -> None:
        msgs = [
            _make_msg(hash="h1", short_hash="h1", changelog_entry="newer"),
            _make_msg(hash="h2", short_hash="h2", changelog_entry="older"),
        ]
        tags = {"h1": "v2.0.0", "h2": "v1.0.0"}
        result = format_changelog(msgs, tags)
        assert result.index("[v2.0.0]") < result.index("[v1.0.0]")


# ===========================================================================
# 5. format_changelog categorizes entries correctly (Added, Changed, Fixed, etc.)
# ===========================================================================


class TestFormatChangelogCategories:
    """format_changelog categorizes entries correctly."""

    def test_all_six_categories_rendered(self) -> None:
        categories = ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"]
        msgs = [
            _make_msg(
                hash=f"h{i}",
                short_hash=f"h{i}",
                changelog_category=cat,
                changelog_entry=f"{cat} entry",
            )
            for i, cat in enumerate(categories)
        ]
        result = format_changelog(msgs, {})
        for cat in categories:
            assert f"### {cat}" in result
            assert f"- {cat} entry" in result

    def test_category_order_follows_keep_a_changelog(self) -> None:
        """Categories appear in standard order: Added, Changed, Deprecated, Removed, Fixed, Security."""
        expected_order = ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"]
        msgs = [
            _make_msg(
                hash=f"h{i}",
                short_hash=f"h{i}",
                changelog_category=cat,
                changelog_entry=f"{cat} entry",
            )
            for i, cat in enumerate(expected_order)
        ]
        result = format_changelog(msgs, {})
        positions = [result.index(f"### {cat}") for cat in expected_order]
        assert positions == sorted(positions), (
            f"Categories not in standard order: {positions}"
        )

    def test_entries_as_bullet_points(self) -> None:
        msgs = [
            _make_msg(changelog_category="Added", changelog_entry="New feature X"),
        ]
        result = format_changelog(msgs, {})
        assert "- New feature X" in result

    def test_multiple_entries_under_same_category(self) -> None:
        msgs = [
            _make_msg(hash="h1", short_hash="h1", changelog_category="Fixed", changelog_entry="Bug A"),
            _make_msg(hash="h2", short_hash="h2", changelog_category="Fixed", changelog_entry="Bug B"),
        ]
        result = format_changelog(msgs, {})
        assert result.count("### Fixed") == 1
        assert "- Bug A" in result
        assert "- Bug B" in result


# ===========================================================================
# 6. format_changelog handles no tags (groups by date / all under Unreleased)
# ===========================================================================


class TestFormatChangelogNoTags:
    """format_changelog handles no tags — everything falls under Unreleased."""

    def test_all_entries_under_unreleased_when_no_tags(self) -> None:
        msgs = [
            _make_msg(hash="h1", short_hash="h1", changelog_entry="entry one"),
            _make_msg(hash="h2", short_hash="h2", changelog_entry="entry two"),
            _make_msg(hash="h3", short_hash="h3", changelog_entry="entry three"),
        ]
        result = format_changelog(msgs, {})
        assert "## [Unreleased]" in result
        # No version sections
        assert "## [v" not in result
        # All entries present
        assert "- entry one" in result
        assert "- entry two" in result
        assert "- entry three" in result

    def test_no_tags_single_entry(self) -> None:
        msgs = [_make_msg(changelog_entry="lone entry")]
        result = format_changelog(msgs, {})
        assert "## [Unreleased]" in result
        assert "- lone entry" in result

    def test_no_tags_empty_dict(self) -> None:
        msgs = [_make_msg()]
        result = format_changelog(msgs, tags={})
        assert "## [Unreleased]" in result

    def test_no_tags_still_has_header(self) -> None:
        msgs = [_make_msg()]
        result = format_changelog(msgs, {})
        assert result.startswith("# Changelog")


# ===========================================================================
# 7. format_changelog adds comparison links when repo_url provided
# ===========================================================================


class TestFormatChangelogComparisonLinks:
    """format_changelog adds comparison links when repo_url provided."""

    def test_unreleased_links_to_head(self) -> None:
        msgs = [
            _make_msg(hash="h1", short_hash="h1", changelog_entry="new"),
            _make_msg(hash="h2", short_hash="h2", changelog_entry="old"),
        ]
        tags = {"h2": "v1.0.0"}
        result = format_changelog(msgs, tags, repo_url="https://github.com/user/repo")
        assert "[Unreleased]: https://github.com/user/repo/compare/v1.0.0...HEAD" in result

    def test_version_links_compare_to_previous(self) -> None:
        msgs = [
            _make_msg(hash="h1", short_hash="h1"),
            _make_msg(hash="h2", short_hash="h2"),
            _make_msg(hash="h3", short_hash="h3"),
        ]
        tags = {"h1": "v3.0.0", "h2": "v2.0.0", "h3": "v1.0.0"}
        result = format_changelog(msgs, tags, repo_url="https://github.com/user/repo")
        assert "[v3.0.0]: https://github.com/user/repo/compare/v2.0.0...v3.0.0" in result
        assert "[v2.0.0]: https://github.com/user/repo/compare/v1.0.0...v2.0.0" in result

    def test_first_version_links_to_release_tag(self) -> None:
        msgs = [_make_msg(hash="h1", short_hash="h1")]
        tags = {"h1": "v1.0.0"}
        result = format_changelog(msgs, tags, repo_url="https://github.com/user/repo")
        assert "[v1.0.0]: https://github.com/user/repo/releases/tag/v1.0.0" in result

    def test_trailing_slash_stripped_from_repo_url(self) -> None:
        msgs = [_make_msg(hash="h1", short_hash="h1")]
        tags = {"h1": "v1.0.0"}
        result = format_changelog(msgs, tags, repo_url="https://github.com/user/repo/")
        assert "https://github.com/user/repo/releases/tag/v1.0.0" in result
        # No double slash
        assert "repo//releases" not in result

    def test_no_links_without_repo_url(self) -> None:
        msgs = [_make_msg(hash="h1", short_hash="h1")]
        tags = {"h1": "v1.0.0"}
        result = format_changelog(msgs, tags, repo_url=None)
        assert "compare" not in result
        assert "releases/tag" not in result

    def test_full_three_version_link_set(self) -> None:
        msgs = [
            _make_msg(hash="h0", short_hash="h0", changelog_entry="unreleased"),
            _make_msg(hash="h1", short_hash="h1", changelog_entry="v2 change"),
            _make_msg(hash="h2", short_hash="h2", changelog_entry="v1 change"),
        ]
        tags = {"h1": "v2.0.0", "h2": "v1.0.0"}
        result = format_changelog(msgs, tags, repo_url="https://github.com/user/repo")
        assert "[Unreleased]: https://github.com/user/repo/compare/v2.0.0...HEAD" in result
        assert "[v2.0.0]: https://github.com/user/repo/compare/v1.0.0...v2.0.0" in result
        assert "[v1.0.0]: https://github.com/user/repo/releases/tag/v1.0.0" in result

    def test_unreleased_only_no_previous_tag(self) -> None:
        """When there's only Unreleased and no tags, link goes to commits/HEAD."""
        msgs = [_make_msg(hash="h1", short_hash="h1")]
        result = format_changelog(msgs, {}, repo_url="https://github.com/user/repo")
        assert "[Unreleased]: https://github.com/user/repo/commits/HEAD" in result


# ===========================================================================
# 8. format_messages shows original vs proposed with correct formatting
# ===========================================================================


class TestFormatMessages:
    """format_messages shows original vs proposed with correct formatting."""

    def test_header_present(self) -> None:
        msgs = [_make_msg()]
        result = format_messages(msgs)
        assert "=== Proposed Commit Messages ===" in result

    def test_commit_numbered_header(self) -> None:
        msgs = [_make_msg(short_hash="abc1234")]
        result = format_messages(msgs)
        assert "--- Commit 1: abc1234 ---" in result

    def test_original_shown_when_commits_provided(self) -> None:
        msgs = [_make_msg()]
        commits = [_make_commit(original_message="wip fix stuff")]
        result = format_messages(msgs, commits)
        assert "Original: wip fix stuff" in result

    def test_proposed_shown(self) -> None:
        msgs = [_make_msg(subject="Fix null pointer in parser")]
        result = format_messages(msgs)
        assert "Proposed: Fix null pointer in parser" in result

    def test_date_and_author_shown_when_commits_provided(self) -> None:
        msgs = [_make_msg()]
        commits = [_make_commit(author="Jane Doe", date=datetime(2025, 3, 10, 14, 0, 0))]
        result = format_messages(msgs, commits)
        assert "Author:   Jane Doe" in result
        assert "Date:     2025-03-10 14:00:00" in result

    def test_no_original_without_commits(self) -> None:
        msgs = [_make_msg()]
        result = format_messages(msgs)
        assert "Original:" not in result
        # Hash fallback shown instead
        assert "Hash:" in result

    def test_body_included_in_proposed(self) -> None:
        msgs = [_make_msg(subject="Add logging", body="Detailed body text here.")]
        result = format_messages(msgs)
        assert "Add logging" in result
        assert "Detailed body text here." in result

    def test_changelog_category_hint(self) -> None:
        msgs = [_make_msg(changelog_category="Added", changelog_entry="New feature")]
        result = format_messages(msgs)
        assert "Category: [Added] New feature" in result

    def test_multiple_commits_indexed(self) -> None:
        msgs = [
            _make_msg(hash="h1", short_hash="h1s"),
            _make_msg(hash="h2", short_hash="h2s"),
            _make_msg(hash="h3", short_hash="h3s"),
        ]
        result = format_messages(msgs)
        assert "Commit 1: h1s" in result
        assert "Commit 2: h2s" in result
        assert "Commit 3: h3s" in result

    def test_empty_messages(self) -> None:
        result = format_messages([])
        assert "=== Proposed Commit Messages ===" in result
        assert "No messages to display." in result

    def test_message_with_commit_pair(self) -> None:
        """Verify commit-message pairing via matching hashes."""
        msgs = [
            _make_msg(hash="h1", short_hash="h1s", subject="Improve perf"),
            _make_msg(hash="h2", short_hash="h2s", subject="Fix typo"),
        ]
        commits = [
            _make_commit(hash="h1", short_hash="h1s", original_message="etc"),
            _make_commit(hash="h2", short_hash="h2s", original_message="wip"),
        ]
        result = format_messages(msgs, commits)
        # Both original and proposed appear for each
        assert "Original: etc" in result
        assert "Proposed: Improve perf" in result
        assert "Original: wip" in result
        assert "Proposed: Fix typo" in result


# ===========================================================================
# 9. format_both combines messages and changelog
# ===========================================================================


class TestFormatBoth:
    """format_both combines messages and changelog."""

    def test_contains_both_sections(self) -> None:
        msgs = [_make_msg()]
        commits = [_make_commit()]
        result = format_both(msgs, commits, {})
        assert "=== Proposed Commit Messages ===" in result
        assert "# Changelog" in result

    def test_separator_present(self) -> None:
        msgs = [_make_msg()]
        commits = [_make_commit()]
        result = format_both(msgs, commits, {})
        assert "=" * 60 in result

    def test_messages_before_changelog(self) -> None:
        msgs = [_make_msg()]
        commits = [_make_commit()]
        result = format_both(msgs, commits, {})
        msg_pos = result.index("=== Proposed Commit Messages ===")
        changelog_pos = result.index("# Changelog")
        assert msg_pos < changelog_pos

    def test_with_tags_and_repo_url(self) -> None:
        msgs = [
            _make_msg(hash="h1", short_hash="h1s", changelog_entry="new stuff"),
            _make_msg(hash="h2", short_hash="h2s", changelog_entry="old stuff"),
        ]
        commits = [
            _make_commit(hash="h1", short_hash="h1s"),
            _make_commit(hash="h2", short_hash="h2s"),
        ]
        tags = {"h2": "v1.0.0"}
        result = format_both(msgs, commits, tags, repo_url="https://github.com/u/r")
        assert "## [Unreleased]" in result
        assert "## [v1.0.0]" in result
        assert "[Unreleased]: https://github.com/u/r/compare/v1.0.0...HEAD" in result

    def test_empty_inputs(self) -> None:
        result = format_both([], [], {})
        assert "No messages to display." in result
        assert "No changes yet." in result


# ===========================================================================
# 10. Edge cases: single commit, no commits, all same category
# ===========================================================================


class TestFormatEdgeCases:
    """Edge cases: single commit, no commits, all same category."""

    # -- Single commit --

    def test_single_commit_changelog(self) -> None:
        msgs = [_make_msg(changelog_entry="Only change")]
        result = format_changelog(msgs, {})
        assert "# Changelog" in result
        assert "## [Unreleased]" in result
        assert "- Only change" in result

    def test_single_commit_messages(self) -> None:
        msgs = [_make_msg(short_hash="aaa")]
        result = format_messages(msgs)
        assert "Commit 1: aaa" in result
        # Only one commit header
        assert "Commit 2" not in result

    def test_single_commit_format_both(self) -> None:
        msgs = [_make_msg()]
        commits = [_make_commit()]
        result = format_both(msgs, commits, {})
        assert "Commit 1:" in result
        assert "## [Unreleased]" in result

    # -- No commits / empty messages --

    def test_no_commits_changelog(self) -> None:
        result = format_changelog([], {})
        assert "# Changelog" in result
        assert "No changes yet." in result

    def test_no_commits_messages(self) -> None:
        result = format_messages([])
        assert "No messages to display." in result

    def test_no_commits_format_both(self) -> None:
        result = format_both([], [], {})
        assert "No messages to display." in result
        assert "No changes yet." in result
        # Separator still present
        assert "=" * 60 in result

    # -- All same category --

    def test_all_same_category_only_one_heading(self) -> None:
        msgs = [
            _make_msg(hash=f"h{i}", short_hash=f"h{i}", changelog_category="Added", changelog_entry=f"Feature {i}")
            for i in range(5)
        ]
        result = format_changelog(msgs, {})
        # Only one "### Added" heading
        assert result.count("### Added") == 1
        # All entries present
        for i in range(5):
            assert f"- Feature {i}" in result

    def test_all_same_category_no_other_category_headings(self) -> None:
        msgs = [
            _make_msg(hash=f"h{i}", short_hash=f"h{i}", changelog_category="Security", changelog_entry=f"Patch {i}")
            for i in range(3)
        ]
        result = format_changelog(msgs, {})
        assert "### Security" in result
        # No other category headings
        for cat in ["Added", "Changed", "Deprecated", "Removed", "Fixed"]:
            assert f"### {cat}" not in result

    def test_all_same_category_different_versions(self) -> None:
        """All entries are 'Fixed' but span multiple versions."""
        msgs = [
            _make_msg(hash="h1", short_hash="h1", changelog_category="Fixed", changelog_entry="Fix A"),
            _make_msg(hash="h2", short_hash="h2", changelog_category="Fixed", changelog_entry="Fix B"),
        ]
        tags = {"h1": "v2.0.0", "h2": "v1.0.0"}
        result = format_changelog(msgs, tags)
        assert "## [v2.0.0]" in result
        assert "## [v1.0.0]" in result
        # Each version section has its own ### Fixed
        assert result.count("### Fixed") == 2

    # -- Miscellaneous edge cases --

    def test_message_body_none_excluded(self) -> None:
        """When body is None, only subject appears in Proposed."""
        msgs = [_make_msg(subject="Subject only", body=None)]
        result = format_messages(msgs)
        assert "Proposed: Subject only" in result
        # Should NOT have a double newline for the body
        lines = result.split("\n")
        for i, line in enumerate(lines):
            if "Proposed:" in line:
                assert line == "Proposed: Subject only"
                break

    def test_changelog_with_all_categories_and_versions(self) -> None:
        """Comprehensive test: multiple categories across multiple versions."""
        msgs = [
            _make_msg(hash="h1", short_hash="h1", changelog_category="Added", changelog_entry="New API"),
            _make_msg(hash="h2", short_hash="h2", changelog_category="Fixed", changelog_entry="Fix crash"),
            _make_msg(hash="h3", short_hash="h3", changelog_category="Added", changelog_entry="New CLI"),
            _make_msg(hash="h4", short_hash="h4", changelog_category="Changed", changelog_entry="Update UI"),
        ]
        tags = {"h2": "v2.0.0", "h4": "v1.0.0"}
        result = format_changelog(msgs, tags, repo_url="https://github.com/test/proj")
        # Unreleased has h1 (Added)
        assert "## [Unreleased]" in result
        # v2.0.0 has h2 (Fixed)
        assert "## [v2.0.0]" in result
        # v1.0.0 has h3 (Added) and h4 (Changed) — based on grouping logic:
        # h3 is untagged so also goes to nearest section...
        # Actually h3 is untagged, so it goes to Unreleased
        # Let me verify: h1 untagged -> Unreleased, h2 tagged v2, h3 untagged -> but comes after h2...
        # _group_messages_by_version walks messages in order:
        #   h1 -> not in tags -> Unreleased
        #   h2 -> tags[h2]=v2.0.0 -> v2.0.0
        #   h3 -> not in tags -> Unreleased (already exists, appends)
        #   h4 -> tags[h4]=v1.0.0 -> v1.0.0
        assert "- New API" in result
        assert "- Fix crash" in result
        assert "- New CLI" in result
        assert "- Update UI" in result
        # Comparison links present
        assert "compare" in result
