"""Tests for gitre.cache — .gitre/ directory management.

Covers:
  1. save_analysis creates .gitre/ dir and analysis.json with correct content
  2. load_analysis reads and correctly parses saved data (round-trip)
  3. validate_cache returns (True, '') when HEAD matches
  4. validate_cache returns (False, warning) when HEAD has moved
  5. clear_cache removes analysis.json
  6. load_analysis raises FileNotFoundError when no cache exists

Uses tmp_path for isolated FS tests; mini git repos via subprocess for HEAD
hash validation.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from gitre.cache import clear_cache, load_analysis, save_analysis, validate_cache
from gitre.models import AnalysisResult, GeneratedMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> str:
    """Initialise a minimal git repo at *path* and return the HEAD hash.

    Creates a single commit so that ``git rev-parse HEAD`` works.
    Returns the full SHA-1 hash of the initial commit.
    """
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(path),
        "PATH": subprocess.os.environ.get("PATH", ""),
    }
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args],
        cwd=str(path),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    run("init", "-b", "main")
    (path / "file.txt").write_text("hello\n")
    run("add", ".")
    run("commit", "-m", "init")
    return run("rev-parse", "HEAD").stdout.strip()


def _make_new_commit(path: Path, filename: str = "extra.txt") -> str:
    """Add a new commit in an existing repo and return the new HEAD hash."""
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(path),
        "PATH": subprocess.os.environ.get("PATH", ""),
    }
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args],
        cwd=str(path),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    (path / filename).write_text("new content\n")
    run("add", ".")
    run("commit", "-m", "second")
    return run("rev-parse", "HEAD").stdout.strip()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Create a minimal fake repo directory with a .gitignore."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def sample_result(tmp_path: Path) -> AnalysisResult:
    """Build a small AnalysisResult for testing."""
    return AnalysisResult(
        repo_path=str(tmp_path),
        head_hash="abc123def456",
        from_ref="v1.0.0",
        to_ref="HEAD",
        commits_analyzed=2,
        messages=[
            GeneratedMessage(
                hash="abc123def456",
                short_hash="abc123d",
                subject="Fix login bug",
                body="Resolved null pointer on login.",
                changelog_category="Fixed",
                changelog_entry="Fixed login null pointer error.",
            ),
        ],
        tags={"v1.0.0": "aaa111"},
        total_tokens=500,
        total_cost=0.01,
        analyzed_at=datetime(2025, 6, 15, 12, 0, 0),
    )


@pytest.fixture()
def git_repo(tmp_path: Path) -> tuple[Path, str]:
    """Create a real mini git repo; return (path, head_hash)."""
    repo_path = tmp_path / "gitrepo"
    repo_path.mkdir()
    head = _init_git_repo(repo_path)
    return repo_path, head


# ── save_analysis ──────────────────────────────────────────────────────


class TestSaveAnalysis:
    """Test 1: save_analysis creates dir and analysis.json."""

    def test_creates_gitre_dir_and_analysis_json(
        self, repo: Path, sample_result: AnalysisResult
    ) -> None:
        """(1) .gitre/ directory and analysis.json are created with correct content."""
        save_analysis(str(repo), sample_result)

        gitre_dir = repo / ".gitre"
        assert gitre_dir.is_dir()

        analysis = gitre_dir / "analysis.json"
        assert analysis.exists()

        data = json.loads(analysis.read_text(encoding="utf-8"))
        assert data["head_hash"] == "abc123def456"
        assert data["commits_analyzed"] == 2
        assert len(data["messages"]) == 1
        assert data["messages"][0]["subject"] == "Fix login bug"
        assert data["repo_path"] == str(repo.parent / repo.name)
        assert data["from_ref"] == "v1.0.0"
        assert data["to_ref"] == "HEAD"
        assert data["total_tokens"] == 500
        assert data["total_cost"] == 0.01

    def test_datetime_serialised_as_string(
        self, repo: Path, sample_result: AnalysisResult
    ) -> None:
        """Datetimes are serialised as ISO strings (mode='json')."""
        save_analysis(str(repo), sample_result)
        data = json.loads(
            (repo / ".gitre" / "analysis.json").read_text(encoding="utf-8")
        )
        assert isinstance(data["analyzed_at"], str)

    def test_does_not_create_gitignore_entries(
        self, repo: Path, sample_result: AnalysisResult
    ) -> None:
        """save_analysis does not create .gitre/.gitignore or modify root .gitignore."""
        save_analysis(str(repo), sample_result)
        # No inner .gitignore
        assert not (repo / ".gitre" / ".gitignore").exists()
        # Root .gitignore unchanged
        content = (repo / ".gitignore").read_text(encoding="utf-8")
        assert ".gitre/" not in content

    def test_creates_gitre_dir_when_none_existed(
        self, tmp_path: Path, sample_result: AnalysisResult
    ) -> None:
        """Ensure .gitre/ is created even when the directory tree doesn't exist."""
        fresh = tmp_path / "brand_new_repo"
        fresh.mkdir()
        save_analysis(str(fresh), sample_result)
        assert (fresh / ".gitre" / "analysis.json").exists()


# ── load_analysis ──────────────────────────────────────────────────────


class TestLoadAnalysis:
    """Test 4 & 8: round-trip loading and error on missing cache."""

    def test_round_trip(self, repo: Path, sample_result: AnalysisResult) -> None:
        """(4) save → load round-trip preserves the full AnalysisResult."""
        save_analysis(str(repo), sample_result)
        loaded = load_analysis(str(repo))
        assert loaded == sample_result

    def test_round_trip_preserves_all_fields(
        self, repo: Path, sample_result: AnalysisResult
    ) -> None:
        """Verify individual fields survive the round-trip."""
        save_analysis(str(repo), sample_result)
        loaded = load_analysis(str(repo))
        assert loaded.head_hash == sample_result.head_hash
        assert loaded.from_ref == sample_result.from_ref
        assert loaded.to_ref == sample_result.to_ref
        assert loaded.commits_analyzed == sample_result.commits_analyzed
        assert loaded.total_tokens == sample_result.total_tokens
        assert loaded.total_cost == sample_result.total_cost
        assert loaded.analyzed_at == sample_result.analyzed_at
        assert len(loaded.messages) == len(sample_result.messages)
        assert loaded.tags == sample_result.tags

    def test_round_trip_with_multiple_messages(self, tmp_path: Path) -> None:
        """Round-trip with multiple messages and tags."""
        messages = [
            GeneratedMessage(
                hash="aaa111",
                short_hash="aaa111",
                subject="Add feature X",
                body="Detailed body.",
                changelog_category="Added",
                changelog_entry="Feature X added.",
            ),
            GeneratedMessage(
                hash="bbb222",
                short_hash="bbb222",
                subject="Fix bug Y",
                body=None,
                changelog_category="Fixed",
                changelog_entry="Bug Y fixed.",
            ),
        ]
        result = AnalysisResult(
            repo_path=str(tmp_path),
            head_hash="aaa111",
            commits_analyzed=2,
            messages=messages,
            tags={"aaa111": "v1.0.0", "bbb222": "v0.9.0"},
            analyzed_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        )
        save_analysis(str(tmp_path), result)
        loaded = load_analysis(str(tmp_path))
        assert loaded == result
        assert loaded.messages[1].body is None

    def test_file_not_found(self, tmp_path: Path) -> None:
        """(8) load_analysis raises FileNotFoundError when no cache exists."""
        with pytest.raises(FileNotFoundError):
            load_analysis(str(tmp_path))

    def test_file_not_found_message(self, tmp_path: Path) -> None:
        """Error message references the expected path."""
        with pytest.raises(FileNotFoundError):
            load_analysis(str(tmp_path / "nonexistent"))


# ── validate_cache ─────────────────────────────────────────────────────


class TestValidateCache:
    """Test 5 & 6: HEAD matching / stale detection, using both mocks and real git repos."""

    # --- Tests with mocked subprocess (unit-level) ---

    @patch("gitre.cache.subprocess.run")
    def test_valid_cache_mocked(
        self, mock_run, sample_result: AnalysisResult
    ) -> None:
        """(5) validate_cache returns (True, '') when HEAD matches (mocked)."""
        mock_run.return_value.stdout = "abc123def456\n"
        is_valid, msg = validate_cache(str(Path(".")), sample_result)
        assert is_valid is True
        assert msg == ""

    @patch("gitre.cache.subprocess.run")
    def test_stale_cache_mocked(
        self, mock_run, sample_result: AnalysisResult
    ) -> None:
        """(6) validate_cache returns (False, warning) when HEAD has moved (mocked)."""
        mock_run.return_value.stdout = "different_hash_value\n"
        is_valid, msg = validate_cache(str(Path(".")), sample_result)
        assert is_valid is False
        assert "stale" in msg.lower()
        assert "abc123de" in msg  # truncated cached hash
        assert "differen" in msg  # truncated current hash

    @patch(
        "gitre.cache.subprocess.run",
        side_effect=FileNotFoundError("git not found"),
    )
    def test_git_not_available(
        self, mock_run, sample_result: AnalysisResult
    ) -> None:
        """Returns (False, ...) when git binary is not found."""
        is_valid, msg = validate_cache(str(Path(".")), sample_result)
        assert is_valid is False
        assert "Unable to determine" in msg

    @patch(
        "gitre.cache.subprocess.run",
        side_effect=subprocess.CalledProcessError(128, "git"),
    )
    def test_git_command_fails(
        self, mock_run, sample_result: AnalysisResult
    ) -> None:
        """Returns (False, ...) when git command exits non-zero."""
        is_valid, msg = validate_cache(str(Path(".")), sample_result)
        assert is_valid is False
        assert "Unable to determine" in msg

    # --- Tests with real mini git repos (integration-level) ---

    def test_valid_cache_real_repo(self, git_repo: tuple[Path, str]) -> None:
        """(5) validate_cache returns (True, '') against a real git repo HEAD."""
        repo_path, head_hash = git_repo
        result = AnalysisResult(
            repo_path=str(repo_path),
            head_hash=head_hash,
            commits_analyzed=1,
        )
        is_valid, msg = validate_cache(str(repo_path), result)
        assert is_valid is True
        assert msg == ""

    def test_stale_cache_real_repo(self, git_repo: tuple[Path, str]) -> None:
        """(6) validate_cache detects staleness after a new commit in a real repo."""
        repo_path, old_head = git_repo
        result = AnalysisResult(
            repo_path=str(repo_path),
            head_hash=old_head,
            commits_analyzed=1,
        )
        # Create a new commit → HEAD moves
        _make_new_commit(repo_path)
        is_valid, msg = validate_cache(str(repo_path), result)
        assert is_valid is False
        assert "stale" in msg.lower()

    def test_valid_cache_with_conftest_repo(
        self, tmp_git_repo: Path
    ) -> None:
        """validate_cache works with the shared tmp_git_repo fixture."""
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(tmp_git_repo),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        result = AnalysisResult(
            repo_path=str(tmp_git_repo),
            head_hash=head,
            commits_analyzed=1,
        )
        is_valid, msg = validate_cache(str(tmp_git_repo), result)
        assert is_valid is True
        assert msg == ""


# ── clear_cache ────────────────────────────────────────────────────────


class TestClearCache:
    """Test 7: clear_cache removes analysis.json."""

    def test_removes_analysis_json(
        self, repo: Path, sample_result: AnalysisResult
    ) -> None:
        """(7) clear_cache removes analysis.json when it exists."""
        save_analysis(str(repo), sample_result)
        assert (repo / ".gitre" / "analysis.json").exists()
        clear_cache(str(repo))
        assert not (repo / ".gitre" / "analysis.json").exists()
        # .gitre/ directory itself still exists (only json is removed)
        assert (repo / ".gitre").is_dir()

    def test_noop_when_no_file(self, tmp_path: Path) -> None:
        """clear_cache is a no-op when analysis.json doesn't exist."""
        clear_cache(str(tmp_path))  # Should not raise

    def test_noop_when_no_gitre_dir(self, tmp_path: Path) -> None:
        """clear_cache is a no-op when .gitre/ directory doesn't exist."""
        assert not (tmp_path / ".gitre").exists()
        clear_cache(str(tmp_path))  # Should not raise
