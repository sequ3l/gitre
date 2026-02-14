"""Shared test fixtures for the gitre test suite.

CRITICAL: The autouse ``_mock_claude_sdk`` fixture globally prevents any real
Claude Agent SDK calls from being made during tests.  Without this, ``query()``
spawns a real Claude Code CLI subprocess, which hangs for 30-60+ seconds per
call, burns compute quota, and makes CI unusable.

The mock target is ``gitre.generator.query`` — the module-level reference —
NOT ``claude_agent_sdk.query``.  Patching the *source* module does not affect
code that has already imported the name.
"""

from __future__ import annotations

import subprocess
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gitre.models import AnalysisResult, CommitInfo, GeneratedMessage

# ---------------------------------------------------------------------------
# Helpers for async-iterable mocking
# ---------------------------------------------------------------------------


class _AsyncIterableFromList:
    """Wrap a list of items as an async iterable (for ``async for``)."""

    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def __aiter__(self):  # noqa: ANN204
        return self

    async def __anext__(self) -> Any:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def make_mock_query(items: list[Any] | None = None) -> MagicMock:
    """Create a mock ``query()`` that returns an async iterable of *items*.

    If *items* is ``None`` (the default), an empty async iterable is returned
    so that any code path exercising ``query()`` receives no messages and
    does not spawn a real subprocess.
    """
    mock = MagicMock()
    mock.return_value = _AsyncIterableFromList(list(items or []))
    return mock


# ---------------------------------------------------------------------------
# Autouse fixture: globally mock Claude SDK query()
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_claude_sdk():
    """Globally prevent real Claude SDK calls in ALL tests.

    This is an autouse fixture — it applies to every test automatically.
    Individual tests that need to verify SDK integration should patch
    with their own mock that returns specific test data.

    The mock returns an empty async iterator by default so that any code
    path exercising ``query()`` gets an empty response rather than
    spawning a real subprocess.
    """
    mock_query = make_mock_query()
    with patch("gitre.generator.query", mock_query):
        yield mock_query


# ---------------------------------------------------------------------------
# Temporary git repository fixture
# ---------------------------------------------------------------------------


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in *cwd* and return the completed process."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "Test Author",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test Author",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "GIT_AUTHOR_DATE": "2026-01-15T10:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-01-15T10:00:00+00:00",
            # Minimal PATH so git can find itself
            "PATH": subprocess.os.environ.get("PATH", ""),
            # Prevent git from reading user-level config
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(cwd),
        },
    )


@pytest.fixture()
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with realistic commit history.

    The repo has:
    - 7 commits with lazy messages ('etc', 'wip', 'fix', 'update', 'stuff', etc.)
    - 2 version tags: v0.1.0 and v0.2.0
    - A mix of small and large diffs (creating and modifying various files)
    - At least 1 merge commit

    Returns the path to the repository root.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    # Initialise
    _run_git(repo, "init", "-b", "main")

    # Commit 1 — create initial files (large diff)
    readme = repo / "README.md"
    readme.write_text("# Test Project\n\nA sample project.\n")
    main_py = repo / "main.py"
    main_py.write_text(textwrap.dedent("""\
        import sys

        def main():
            print("hello world")
            return 0

        if __name__ == "__main__":
            sys.exit(main())
    """))
    config = repo / "config.yaml"
    config.write_text("debug: false\nlog_level: info\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "etc")

    # Tag v0.1.0 on the initial commit
    _run_git(repo, "tag", "v0.1.0")

    # Commit 2 — small fix in main.py
    main_py.write_text(textwrap.dedent("""\
        import sys

        def main():
            print("hello world!")
            return 0

        if __name__ == "__main__":
            sys.exit(main())
    """))
    _run_git(repo, "add", "main.py")
    _run_git(repo, "commit", "-m", "fix")

    # Commit 3 — add a new file (medium diff)
    utils = repo / "utils.py"
    utils.write_text(textwrap.dedent("""\
        \"\"\"Utility helpers.\"\"\"

        def slugify(text: str) -> str:
            return text.lower().replace(" ", "-")

        def truncate(text: str, length: int = 80) -> str:
            if len(text) <= length:
                return text
            return text[:length - 3] + "..."
    """))
    _run_git(repo, "add", "utils.py")
    _run_git(repo, "commit", "-m", "wip")

    # Commit 4 — update config (small diff)
    config.write_text("debug: true\nlog_level: debug\nmax_retries: 3\n")
    _run_git(repo, "add", "config.yaml")
    _run_git(repo, "commit", "-m", "update")

    # --- Create a feature branch for the merge ---
    _run_git(repo, "checkout", "-b", "feature/docs")

    # Commit 5 (on feature branch) — add docs
    docs_dir = repo / "docs"
    docs_dir.mkdir()
    (docs_dir / "guide.md").write_text(
        "# User Guide\n\n## Installation\n\nRun `pip install .`\n\n"
        "## Configuration\n\nEdit `config.yaml`.\n"
    )
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "stuff")

    # Switch back to main
    _run_git(repo, "checkout", "main")

    # Commit 6 (on main) — large diff, add tests file
    tests_file = repo / "tests.py"
    tests_file.write_text(textwrap.dedent("""\
        import unittest
        from utils import slugify, truncate

        class TestSlugify(unittest.TestCase):
            def test_basic(self):
                self.assertEqual(slugify("Hello World"), "hello-world")

            def test_already_slug(self):
                self.assertEqual(slugify("hello"), "hello")

        class TestTruncate(unittest.TestCase):
            def test_short(self):
                self.assertEqual(truncate("hi", 10), "hi")

            def test_long(self):
                result = truncate("a" * 100, 20)
                self.assertEqual(len(result), 20)
                self.assertTrue(result.endswith("..."))

        if __name__ == "__main__":
            unittest.main()
    """))
    _run_git(repo, "add", "tests.py")
    _run_git(repo, "commit", "-m", "wip")

    # Merge the feature branch → creates a merge commit
    _run_git(repo, "merge", "feature/docs", "--no-ff", "-m", "update")

    # Tag v0.2.0 after the merge
    _run_git(repo, "tag", "v0.2.0")

    return repo


# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_commit() -> CommitInfo:
    """A realistic ``CommitInfo`` for use in generator tests."""
    return CommitInfo(
        hash="abc1234567890abcdef1234567890abcdef123456",
        short_hash="abc1234",
        author="Test Author <test@example.com>",
        date=datetime(2026, 2, 12, 16, 45, 0, tzinfo=UTC),
        original_message="etc",
        diff_stat=" src/main.py | 10 +++++++---\n 1 file changed, 7 insertions(+), 3 deletions(-)",
        diff_patch=(
            "diff --git a/src/main.py b/src/main.py\n"
            "index 1234567..abcdefg 100644\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1,5 +1,9 @@\n"
            " import os\n"
            "+import sys\n"
            " \n"
            " def main():\n"
            "-    print('hello')\n"
            "+    print('hello world')\n"
            "+    if len(sys.argv) > 1:\n"
            "+        print(f'Args: {sys.argv[1:]}')\n"
            "+    return 0\n"
        ),
        files_changed=1,
        insertions=7,
        deletions=3,
    )


@pytest.fixture()
def sample_commit_2() -> CommitInfo:
    """A second ``CommitInfo`` for batch tests."""
    return CommitInfo(
        hash="def5678901234567890abcdef1234567890abcdef",
        short_hash="def5678",
        author="Test Author <test@example.com>",
        date=datetime(2026, 2, 12, 17, 13, 0, tzinfo=UTC),
        original_message="fix",
        diff_stat=" README.md | 3 ++-\n 1 file changed, 2 insertions(+), 1 deletion(-)",
        diff_patch=(
            "diff --git a/README.md b/README.md\n"
            "index aaa1111..bbb2222 100644\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1,3 +1,4 @@\n"
            " # Project\n"
            "-Description\n"
            "+A CLI tool for reconstructing git history.\n"
            "+\n"
            "+## Usage\n"
        ),
        files_changed=1,
        insertions=2,
        deletions=1,
    )


@pytest.fixture()
def sample_commit_info() -> CommitInfo:
    """A ``CommitInfo`` instance with comprehensive test data.

    Distinct from ``sample_commit`` — includes tags and a multi-file diff to
    exercise more model fields.
    """
    return CommitInfo(
        hash="f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0",
        short_hash="f1a2b3c",
        author="Jane Doe <jane@example.com>",
        date=datetime(2026, 2, 14, 9, 30, 0, tzinfo=UTC),
        original_message="stuff",
        diff_stat=(
            " src/app.py   | 15 +++++++++++----\n"
            " src/utils.py |  8 ++++++--\n"
            " 2 files changed, 17 insertions(+), 6 deletions(-)"
        ),
        diff_patch=(
            "diff --git a/src/app.py b/src/app.py\n"
            "index aaa1111..bbb2222 100644\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -10,8 +10,19 @@\n"
            " from flask import Flask\n"
            "+from flask import jsonify\n"
            " \n"
            " app = Flask(__name__)\n"
            " \n"
            "+@app.route('/health')\n"
            "+def health():\n"
            "+    return jsonify(status='ok')\n"
            "+\n"
            " @app.route('/')\n"
            " def index():\n"
            "-    return 'hello'\n"
            "+    return jsonify(message='hello world')\n"
            "diff --git a/src/utils.py b/src/utils.py\n"
            "index ccc3333..ddd4444 100644\n"
            "--- a/src/utils.py\n"
            "+++ b/src/utils.py\n"
            "@@ -1,5 +1,11 @@\n"
            "+import logging\n"
            "+\n"
            "+logger = logging.getLogger(__name__)\n"
            " \n"
            " def retry(fn, times=3):\n"
            "-    pass\n"
            "+    for attempt in range(times):\n"
            "+        try:\n"
            "+            return fn()\n"
            "+        except Exception:\n"
            "+            logger.warning('Attempt %d failed', attempt + 1)\n"
        ),
        files_changed=2,
        insertions=17,
        deletions=6,
        tags=["v0.3.0", "release-candidate"],
    )


@pytest.fixture()
def mock_claude_response() -> dict:
    """A realistic Claude response dict with all expected fields."""
    return {
        "subject": "Add health-check endpoint and retry logic",
        "body": (
            "Introduce a /health route returning JSON status and implement\n"
            "a retry helper with configurable attempt count and logging."
        ),
        "changelog_category": "Added",
        "changelog_entry": (
            "Health-check endpoint at /health and a retry utility with "
            "logging for transient failures."
        ),
    }


@pytest.fixture()
def mock_claude_single_response() -> dict:
    """A realistic single-commit Claude response as a parsed dict."""
    return {
        "subject": "Add argument parsing to main entry point",
        "body": "Extend main() to accept and display command-line arguments",
        "changelog_category": "Added",
        "changelog_entry": "Command-line argument parsing in the main entry point",
    }


@pytest.fixture()
def mock_claude_batch_response() -> list[dict]:
    """A realistic batch Claude response as a parsed list of dicts."""
    return [
        {
            "subject": "Add argument parsing to main entry point",
            "body": "Extend main() to accept and display command-line arguments",
            "changelog_category": "Added",
            "changelog_entry": "Command-line argument parsing in the main entry point",
        },
        {
            "subject": "Update README with project description",
            "body": None,
            "changelog_category": "Changed",
            "changelog_entry": "Improved README with project description and usage section",
        },
    ]


@pytest.fixture()
def sample_generated_message() -> GeneratedMessage:
    """A ``GeneratedMessage`` instance with realistic test data."""
    return GeneratedMessage(
        hash="abc1234567890abcdef1234567890abcdef123456",
        short_hash="abc1234",
        subject="Add argument parsing to main entry point",
        body=(
            "Extend main() to accept and display command-line arguments."
            "\n\nThis allows users to pass flags at the CLI."
        ),
        changelog_category="Added",
        changelog_entry="Command-line argument parsing in the main entry point.",
    )


@pytest.fixture()
def sample_analysis_result() -> AnalysisResult:
    """An ``AnalysisResult`` instance with multiple messages and tags."""
    msg1 = GeneratedMessage(
        hash="abc1234567890abcdef1234567890abcdef123456",
        short_hash="abc1234",
        subject="Add argument parsing to main entry point",
        body="Extend main() to accept and display command-line arguments.",
        changelog_category="Added",
        changelog_entry="Command-line argument parsing in the main entry point.",
    )
    msg2 = GeneratedMessage(
        hash="def5678901234567890abcdef1234567890abcdef",
        short_hash="def5678",
        subject="Fix off-by-one error in pagination",
        body=None,
        changelog_category="Fixed",
        changelog_entry="Corrected off-by-one error that skipped the last page.",
    )
    msg3 = GeneratedMessage(
        hash="aaa9999000011112222333344445555666677778",
        short_hash="aaa9999",
        subject="Remove deprecated config loader",
        body="The legacy YAML loader has been replaced by the TOML parser.",
        changelog_category="Removed",
        changelog_entry="Removed deprecated YAML configuration loader.",
    )
    return AnalysisResult(
        repo_path="/home/user/projects/sample",
        head_hash="abc1234567890abcdef1234567890abcdef123456",
        from_ref="v0.1.0",
        to_ref="v0.2.0",
        commits_analyzed=3,
        messages=[msg1, msg2, msg3],
        tags={
            "abc1234567890abcdef1234567890abcdef123456": "v0.2.0",
            "def5678901234567890abcdef1234567890abcdef": "v0.1.1",
        },
        total_tokens=4500,
        total_cost=0.0135,
        analyzed_at=datetime(2026, 2, 14, 12, 0, 0, tzinfo=UTC),
    )
