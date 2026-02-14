"""Tests for gitre.analyzer — git history walker and diff extraction.

All tests use real temporary git repositories created by the ``tmp_git_repo``
fixture defined in ``conftest.py``.  No mocking of git commands is needed.

Fixture repo layout (7 commits on main after merge):
    1. "etc"    — root commit: README.md, main.py, config.yaml  → tag v0.1.0
    2. "fix"    — modify main.py (small change)
    3. "wip"    — add utils.py
    4. "update" — modify config.yaml
    5. "stuff"  — (on feature/docs branch) add docs/guide.md
    6. "wip"    — (on main) add tests.py
    7. "update" — merge commit (feature/docs → main, --no-ff)  → tag v0.2.0

After the merge, the linear history on main is:
    etc → fix → wip → update → wip → update(merge)
with commit 5 ("stuff") reachable through the merge.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitre.analyzer import (
    enrich_commit,
    get_commits,
    get_diff,
    truncate_diff,
)
from gitre.models import CommitInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_log_hashes(repo: Path) -> list[str]:
    """Return commit hashes in chronological (oldest-first) order."""
    result = subprocess.run(
        ["git", "log", "--reverse", "--format=%H"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return [h.strip() for h in result.stdout.strip().splitlines() if h.strip()]


def _git_rev_parse(repo: Path, ref: str) -> str:
    """Resolve *ref* to a full SHA."""
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# ===================================================================
# 1. get_commits returns all commits in correct order
# ===================================================================


class TestGetCommitsAllInOrder:
    """get_commits() returns every commit in chronological order."""

    def test_returns_all_commits(self, tmp_git_repo: Path) -> None:
        """All reachable commits from HEAD are returned."""
        commits = get_commits(str(tmp_git_repo))
        # The fixture creates 7 commits (6 on main + 1 merge which also
        # brings in the feature branch commit).  git log --reverse on main
        # should list all 7.
        assert len(commits) == 7

    def test_chronological_order(self, tmp_git_repo: Path) -> None:
        """Commits are in oldest-first (chronological) order."""
        commits = get_commits(str(tmp_git_repo))
        # The first commit should be the root ("etc") and the last
        # should be the merge ("update").
        assert commits[0].original_message == "etc"
        assert commits[-1].original_message == "update"

    def test_messages_match_expected_sequence(self, tmp_git_repo: Path) -> None:
        """Commit messages match the known fixture sequence."""
        commits = get_commits(str(tmp_git_repo))
        messages = [c.original_message.split("\n")[0] for c in commits]
        # The merge brings the "stuff" commit into the history.
        # git log --reverse may interleave branches, but the first-parent
        # commits and the feature commit should all appear.
        assert "etc" in messages
        assert "fix" in messages
        assert "stuff" in messages

    def test_commit_fields_populated(self, tmp_git_repo: Path) -> None:
        """Each CommitInfo has hash, short_hash, author, date, message."""
        commits = get_commits(str(tmp_git_repo))
        for c in commits:
            assert len(c.hash) == 40
            assert len(c.short_hash) >= 7
            assert c.author  # non-empty
            assert c.date is not None
            assert c.original_message  # non-empty


# ===================================================================
# 2. get_commits respects --from and --to range
# ===================================================================


class TestGetCommitsRange:
    """get_commits() correctly filters by from_ref / to_ref."""

    def test_from_tag_to_head(self, tmp_git_repo: Path) -> None:
        """from_ref=v0.1.0 excludes the tagged commit and earlier."""
        commits = get_commits(str(tmp_git_repo), from_ref="v0.1.0")
        messages = [c.original_message.split("\n")[0] for c in commits]
        # v0.1.0 is on commit 1 ("etc"), so it should be excluded.
        assert "etc" not in messages
        assert len(commits) >= 1

    def test_from_to_tag_range(self, tmp_git_repo: Path) -> None:
        """from_ref=v0.1.0, to_ref=v0.2.0 returns only commits between tags."""
        commits = get_commits(
            str(tmp_git_repo), from_ref="v0.1.0", to_ref="v0.2.0"
        )
        messages = [c.original_message.split("\n")[0] for c in commits]
        # "etc" (v0.1.0) should NOT be included; "update" (merge, v0.2.0) should.
        assert "etc" not in messages
        assert len(commits) >= 2

    def test_to_ref_only(self, tmp_git_repo: Path) -> None:
        """to_ref=v0.1.0 returns only the root commit."""
        commits = get_commits(str(tmp_git_repo), to_ref="v0.1.0")
        assert len(commits) == 1
        assert commits[0].original_message == "etc"

    def test_same_from_to_returns_empty(self, tmp_git_repo: Path) -> None:
        """When from_ref == to_ref, the range is empty."""
        commits = get_commits(
            str(tmp_git_repo), from_ref="v0.1.0", to_ref="v0.1.0"
        )
        assert commits == []


# ===================================================================
# 3. get_commits detects version tags on commits
# ===================================================================


class TestGetCommitsDetectsTags:
    """get_commits() populates the ``tags`` field from version tags."""

    def test_root_commit_has_v010(self, tmp_git_repo: Path) -> None:
        """The first commit is tagged v0.1.0."""
        commits = get_commits(str(tmp_git_repo))
        root = commits[0]
        assert "v0.1.0" in root.tags

    def test_merge_commit_has_v020(self, tmp_git_repo: Path) -> None:
        """The merge commit is tagged v0.2.0."""
        commits = get_commits(str(tmp_git_repo))
        merge = commits[-1]
        assert "v0.2.0" in merge.tags

    def test_untagged_commits_have_no_tags(self, tmp_git_repo: Path) -> None:
        """Commits without tags have an empty tags list."""
        commits = get_commits(str(tmp_git_repo))
        # Find the "fix" commit — it should have no tags.
        fix_commits = [c for c in commits if c.original_message == "fix"]
        assert len(fix_commits) == 1
        assert fix_commits[0].tags == []


# ===================================================================
# 4. get_diff returns correct diff_stat and diff_patch for normal commit
# ===================================================================


class TestGetDiffNormalCommit:
    """get_diff() produces correct stat and patch for a regular commit."""

    def test_diff_stat_not_empty(self, tmp_git_repo: Path) -> None:
        """diff_stat is non-empty for a regular commit."""
        commits = get_commits(str(tmp_git_repo))
        # Commit 2 ("fix") modifies main.py — a straightforward single-parent commit.
        fix_commit = [c for c in commits if c.original_message == "fix"][0]
        stat, patch = get_diff(str(tmp_git_repo), fix_commit.hash)
        assert stat  # non-empty
        assert "main.py" in stat

    def test_diff_patch_contains_diff_header(self, tmp_git_repo: Path) -> None:
        """diff_patch starts with a standard git diff header."""
        commits = get_commits(str(tmp_git_repo))
        fix_commit = [c for c in commits if c.original_message == "fix"][0]
        _stat, patch = get_diff(str(tmp_git_repo), fix_commit.hash)
        assert "diff --git" in patch

    def test_diff_patch_contains_change(self, tmp_git_repo: Path) -> None:
        """diff_patch contains the actual change lines."""
        commits = get_commits(str(tmp_git_repo))
        fix_commit = [c for c in commits if c.original_message == "fix"][0]
        _stat, patch = get_diff(str(tmp_git_repo), fix_commit.hash)
        # The fix commit changes 'hello world' to 'hello world!' in main.py.
        assert "hello world" in patch


# ===================================================================
# 5. get_diff handles root commit (diff against empty tree)
# ===================================================================


class TestGetDiffRootCommit:
    """get_diff() handles a root commit (no parent) gracefully."""

    def test_root_commit_stat_not_empty(self, tmp_git_repo: Path) -> None:
        """The root commit has a non-empty stat."""
        commits = get_commits(str(tmp_git_repo))
        root = commits[0]
        stat, _patch = get_diff(str(tmp_git_repo), root.hash)
        assert stat  # non-empty

    def test_root_commit_lists_initial_files(self, tmp_git_repo: Path) -> None:
        """The root commit diff shows all initially created files."""
        commits = get_commits(str(tmp_git_repo))
        root = commits[0]
        stat, patch = get_diff(str(tmp_git_repo), root.hash)
        # Root commit created README.md, main.py, config.yaml.
        assert "README.md" in stat
        assert "main.py" in stat
        assert "config.yaml" in stat

    def test_root_commit_patch_has_additions(self, tmp_git_repo: Path) -> None:
        """The root commit patch shows only additions (no deletions of existing content)."""
        commits = get_commits(str(tmp_git_repo))
        root = commits[0]
        _stat, patch = get_diff(str(tmp_git_repo), root.hash)
        # All lines are additions in a root commit.
        assert "diff --git" in patch
        # The patch should contain added content from the files.
        assert "+# Test Project" in patch


# ===================================================================
# 6. get_diff handles merge commits
# ===================================================================


class TestGetDiffMergeCommit:
    """get_diff() handles merge commits (empty or special message)."""

    def test_merge_commit_returns_note(self, tmp_git_repo: Path) -> None:
        """Merge commits return a descriptive note instead of a diff."""
        commits = get_commits(str(tmp_git_repo))
        merge = commits[-1]  # The last commit is the merge.
        stat, patch = get_diff(str(tmp_git_repo), merge.hash)
        assert "merge commit" in stat.lower()
        assert "diff omitted" in patch.lower()

    def test_merge_commit_no_diff_content(self, tmp_git_repo: Path) -> None:
        """Merge commit diff does not contain actual diff --git headers."""
        commits = get_commits(str(tmp_git_repo))
        merge = commits[-1]
        _stat, patch = get_diff(str(tmp_git_repo), merge.hash)
        assert "diff --git" not in patch


# ===================================================================
# 7. truncate_diff truncates large diffs at max_bytes and appends marker
# ===================================================================


class TestTruncateDiffLarge:
    """truncate_diff() correctly truncates oversized diffs."""

    def test_truncates_at_max_bytes(self) -> None:
        """A diff exceeding max_bytes is cut and the marker appended."""
        diff = "x" * 200
        result = truncate_diff(diff, max_bytes=100)
        assert result.endswith("\n[diff truncated]")
        # The content before the marker should be at most 100 bytes.
        content_before_marker = result[: result.index("\n[diff truncated]")]
        assert len(content_before_marker.encode("utf-8")) <= 100

    def test_truncated_result_shorter_than_original(self) -> None:
        """The truncated output (excluding marker) is shorter than original."""
        diff = "a" * 10_000
        result = truncate_diff(diff, max_bytes=500)
        assert len(result) < len(diff) + len("\n[diff truncated]")

    def test_multibyte_truncation(self) -> None:
        """Multi-byte UTF-8 chars are handled without errors on truncation."""
        # Each emoji is 4 bytes.
        diff = "\U0001f600" * 100  # 400 bytes
        result = truncate_diff(diff, max_bytes=50)
        assert result.endswith("[diff truncated]")
        # Must not raise.

    def test_truncation_with_real_git_diff(self, tmp_git_repo: Path) -> None:
        """Truncate an actual diff from the repo."""
        commits = get_commits(str(tmp_git_repo))
        root = commits[0]
        _stat, patch = get_diff(str(tmp_git_repo), root.hash)
        # Truncate to a tiny size.
        result = truncate_diff(patch, max_bytes=20)
        assert result.endswith("[diff truncated]")


# ===================================================================
# 8. truncate_diff passes through small diffs unchanged
# ===================================================================


class TestTruncateDiffSmall:
    """truncate_diff() returns small diffs untouched."""

    def test_small_diff_unchanged(self) -> None:
        """A diff under the limit is returned as-is."""
        diff = "small diff"
        assert truncate_diff(diff, max_bytes=1000) == diff

    def test_exact_limit_unchanged(self) -> None:
        """A diff exactly at the byte limit is not truncated."""
        diff = "x" * 100
        assert truncate_diff(diff, max_bytes=100) == diff

    def test_empty_diff_unchanged(self) -> None:
        """An empty diff is returned as-is."""
        assert truncate_diff("", max_bytes=100) == ""

    def test_default_limit_passes_normal_diff(self, tmp_git_repo: Path) -> None:
        """A typical repo diff is well under the default 50 KB limit."""
        commits = get_commits(str(tmp_git_repo))
        root = commits[0]
        _stat, patch = get_diff(str(tmp_git_repo), root.hash)
        result = truncate_diff(patch)  # default max_bytes=50_000
        # The fixture's diffs are tiny, so no truncation should occur.
        assert result == patch
        assert "[diff truncated]" not in result


# ===================================================================
# 9. enrich_commit fills in all diff fields correctly
# ===================================================================


class TestEnrichCommit:
    """enrich_commit() populates diff_stat, diff_patch, and stat fields."""

    def test_enriches_normal_commit(self, tmp_git_repo: Path) -> None:
        """A normal commit gets all diff fields filled in."""
        commits = get_commits(str(tmp_git_repo))
        fix_commit = [c for c in commits if c.original_message == "fix"][0]

        # Before enrichment, diff fields are empty placeholders.
        assert fix_commit.diff_stat == ""
        assert fix_commit.diff_patch == ""

        enriched = enrich_commit(str(tmp_git_repo), fix_commit)

        # After enrichment, fields are populated.
        assert enriched.diff_stat != ""
        assert "main.py" in enriched.diff_stat
        assert "diff --git" in enriched.diff_patch
        assert enriched.files_changed >= 1
        assert enriched.insertions >= 0
        assert enriched.deletions >= 0

    def test_enriches_root_commit(self, tmp_git_repo: Path) -> None:
        """The root commit gets valid diff fields via empty-tree diff."""
        commits = get_commits(str(tmp_git_repo))
        root = commits[0]
        enriched = enrich_commit(str(tmp_git_repo), root)

        assert enriched.diff_stat != ""
        assert enriched.diff_patch != ""
        # Root commit created 3 files.
        assert enriched.files_changed == 3
        assert enriched.insertions > 0

    def test_enriches_merge_commit(self, tmp_git_repo: Path) -> None:
        """Merge commits get the special note and zero stats."""
        commits = get_commits(str(tmp_git_repo))
        merge = commits[-1]
        enriched = enrich_commit(str(tmp_git_repo), merge)

        assert "merge commit" in enriched.diff_stat.lower()
        assert enriched.files_changed == 0
        assert enriched.insertions == 0
        assert enriched.deletions == 0

    def test_original_commit_not_mutated(self, tmp_git_repo: Path) -> None:
        """enrich_commit returns a new CommitInfo; the original is untouched."""
        commits = get_commits(str(tmp_git_repo))
        fix_commit = [c for c in commits if c.original_message == "fix"][0]
        enriched = enrich_commit(str(tmp_git_repo), fix_commit)

        # Original is unchanged (frozen model).
        assert fix_commit.diff_stat == ""
        assert fix_commit.diff_patch == ""
        assert fix_commit.files_changed == 0

        # Enriched is different.
        assert enriched.diff_stat != ""
        assert enriched is not fix_commit

    def test_enriched_preserves_metadata(self, tmp_git_repo: Path) -> None:
        """enrich_commit preserves hash, author, date, tags, message."""
        commits = get_commits(str(tmp_git_repo))
        root = commits[0]
        enriched = enrich_commit(str(tmp_git_repo), root)

        assert enriched.hash == root.hash
        assert enriched.short_hash == root.short_hash
        assert enriched.author == root.author
        assert enriched.date == root.date
        assert enriched.original_message == root.original_message
        assert enriched.tags == root.tags


# ===================================================================
# 10. Handle non-UTF-8 content gracefully
# ===================================================================


class TestNonUtf8Content:
    """Analyzer handles non-UTF-8 file content without crashing."""

    def test_binary_content_in_diff(self, tmp_path: Path) -> None:
        """A commit with binary (non-UTF-8) content does not crash get_diff."""
        repo = tmp_path / "binary_repo"
        repo.mkdir()

        env = {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@t.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@t.com",
            "PATH": subprocess.os.environ.get("PATH", ""),
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(repo),
        }

        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=str(repo), check=True, capture_output=True, env=env,
        )

        # Write raw bytes that are NOT valid UTF-8.
        binary_file = repo / "data.bin"
        binary_file.write_bytes(b"\x80\x81\x82\xff\xfe\xfd" * 100)

        subprocess.run(
            ["git", "add", "."],
            cwd=str(repo), check=True, capture_output=True, env=env,
        )
        subprocess.run(
            ["git", "commit", "-m", "add binary"],
            cwd=str(repo), check=True, capture_output=True, env=env,
        )

        # This must not raise.
        commits = get_commits(str(repo))
        assert len(commits) == 1

        stat, patch = get_diff(str(repo), commits[0].hash)
        # Binary diffs may show "Binary files differ" or similar.
        assert stat is not None
        assert patch is not None

    def test_non_utf8_filename(self, tmp_path: Path) -> None:
        """A file whose diff contains replacement chars is handled."""
        repo = tmp_path / "encoding_repo"
        repo.mkdir()

        env = {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@t.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@t.com",
            "PATH": subprocess.os.environ.get("PATH", ""),
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(repo),
        }

        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=str(repo), check=True, capture_output=True, env=env,
        )

        # Write a file with non-UTF-8 bytes embedded in text content.
        bad_file = repo / "notes.txt"
        bad_file.write_bytes(b"Hello \x80\x81 World\n")

        subprocess.run(
            ["git", "add", "."],
            cwd=str(repo), check=True, capture_output=True, env=env,
        )
        subprocess.run(
            ["git", "commit", "-m", "add notes"],
            cwd=str(repo), check=True, capture_output=True, env=env,
        )

        commits = get_commits(str(repo))
        assert len(commits) == 1

        # Should not raise despite non-UTF-8 content in diff.
        stat, patch = get_diff(str(repo), commits[0].hash)
        assert isinstance(stat, str)
        assert isinstance(patch, str)

    def test_truncate_diff_with_replacement_chars(self) -> None:
        """truncate_diff handles strings containing replacement characters."""
        # Simulate what errors='replace' produces.
        diff = "Line 1\nLine with \ufffd\ufffd bytes\nLine 3\n" * 100
        result = truncate_diff(diff, max_bytes=50)
        assert result.endswith("[diff truncated]")

    def test_enrich_commit_with_binary(self, tmp_path: Path) -> None:
        """enrich_commit works on a commit that adds binary content."""
        repo = tmp_path / "enrich_bin_repo"
        repo.mkdir()

        env = {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@t.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@t.com",
            "PATH": subprocess.os.environ.get("PATH", ""),
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(repo),
        }

        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=str(repo), check=True, capture_output=True, env=env,
        )

        # Create a binary file and a text file.
        (repo / "image.bin").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
        (repo / "readme.txt").write_text("Hello\n")

        subprocess.run(
            ["git", "add", "."],
            cwd=str(repo), check=True, capture_output=True, env=env,
        )
        subprocess.run(
            ["git", "commit", "-m", "add files"],
            cwd=str(repo), check=True, capture_output=True, env=env,
        )

        commits = get_commits(str(repo))
        enriched = enrich_commit(str(repo), commits[0])

        # Binary file counts as a changed file but with 0 insertions/deletions.
        assert enriched.files_changed == 2
        assert enriched.insertions >= 1  # at least from readme.txt
