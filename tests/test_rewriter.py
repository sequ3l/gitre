"""Tests for gitre.rewriter — git history rewriting module.

Uses ``tmp_git_repo`` fixture for real repo operations where possible, and mocks
for functions that call external tools (git-filter-repo) or require user input.

Tests requiring ``git-filter-repo`` are marked with ``pytest.mark.skipif`` so the
suite stays green on machines without the tool installed.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from gitre.models import CommitInfo, GeneratedMessage
from gitre.rewriter import (
    _build_commit_callback,
    build_message_callback,
    check_filter_repo,
    commit_artifacts,
    confirm_rewrite,
    create_backup,
    display_proposals,
    force_push,
    restore_remotes,
    rewrite_history,
    save_remotes,
    write_changelog,
)

# ---------------------------------------------------------------------------
# Detect git-filter-repo availability for skipif markers
# ---------------------------------------------------------------------------

def _has_filter_repo() -> bool:
    """Return True if git-filter-repo is available on the system."""
    try:
        result = subprocess.run(
            ["git", "filter-repo", "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


HAS_FILTER_REPO = _has_filter_repo()
requires_filter_repo = pytest.mark.skipif(
    not HAS_FILTER_REPO,
    reason="git-filter-repo is not installed",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_msg(
    *,
    hash: str = "abc123def456",
    short_hash: str = "abc123d",
    subject: str = "fix: resolve null pointer",
    body: str | None = None,
    changelog_category: str = "Fixed",
    changelog_entry: str = "Resolved null pointer in parser",
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
    hash: str = "abc123def456",
    short_hash: str = "abc123d",
    original_message: str = "fixed stuff",
) -> CommitInfo:
    return CommitInfo(
        hash=hash,
        short_hash=short_hash,
        author="Test User",
        date=datetime(2024, 1, 15, tzinfo=UTC),
        original_message=original_message,
        diff_stat="1 file changed, 2 insertions(+)",
        diff_patch="diff --git ...",
        files_changed=1,
        insertions=2,
        deletions=0,
    )


# ===========================================================================
# 1. check_filter_repo — returns correct bool based on availability
# ===========================================================================


class TestCheckFilterRepo:
    """Tests for check_filter_repo()."""

    @patch("gitre.rewriter.subprocess.run")
    def test_returns_true_when_available(self, mock_run: MagicMock) -> None:
        """Should return True when git-filter-repo exits with code 0."""
        mock_run.return_value = MagicMock(returncode=0)
        assert check_filter_repo() is True
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][0] == ["git", "filter-repo", "--version"]

    @patch("gitre.rewriter.subprocess.run")
    def test_returns_false_on_nonzero_exit(self, mock_run: MagicMock) -> None:
        """Should return False when git-filter-repo exits with non-zero code."""
        mock_run.return_value = MagicMock(returncode=1)
        assert check_filter_repo() is False

    @patch("gitre.rewriter.subprocess.run", side_effect=FileNotFoundError)
    def test_returns_false_on_file_not_found(self, mock_run: MagicMock) -> None:
        """Should return False when git binary is not found."""
        assert check_filter_repo() is False

    @patch(
        "gitre.rewriter.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=15),
    )
    def test_returns_false_on_timeout(self, mock_run: MagicMock) -> None:
        """Should return False when subprocess times out."""
        assert check_filter_repo() is False

    @patch("gitre.rewriter.subprocess.run", side_effect=OSError("boom"))
    def test_returns_false_on_os_error(self, mock_run: MagicMock) -> None:
        """Should return False on generic OSError."""
        assert check_filter_repo() is False

    @patch("gitre.rewriter.subprocess.run")
    def test_passes_correct_timeout(self, mock_run: MagicMock) -> None:
        """Should pass timeout=15 to subprocess.run."""
        mock_run.return_value = MagicMock(returncode=0)
        check_filter_repo()
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 15

    @patch("gitre.rewriter.subprocess.run")
    def test_captures_output(self, mock_run: MagicMock) -> None:
        """Should set capture_output=True and text=True."""
        mock_run.return_value = MagicMock(returncode=0)
        check_filter_repo()
        _, kwargs = mock_run.call_args
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True


# ===========================================================================
# 2. create_backup — creates a branch with correct naming pattern
# ===========================================================================


class TestCreateBackup:
    """Tests for create_backup()."""

    @patch("gitre.rewriter.subprocess.run")
    def test_creates_branch_and_returns_name(self, mock_run: MagicMock) -> None:
        """Should call 'git branch' and return the backup branch name."""
        mock_run.return_value = MagicMock(returncode=0)
        name = create_backup("/fake/repo")
        assert name.startswith("gitre-backup-")
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0][0:2] == ["git", "branch"]
        assert call_args[1]["cwd"] == "/fake/repo"
        assert call_args[1]["check"] is True

    @patch("gitre.rewriter.subprocess.run")
    def test_branch_name_contains_timestamp_pattern(self, mock_run: MagicMock) -> None:
        """Branch name should match gitre-backup-YYYYMMDDTHHMMSSz."""
        mock_run.return_value = MagicMock(returncode=0)
        name = create_backup("/fake/repo")
        parts = name.split("-", 2)
        assert parts[0] == "gitre"
        assert parts[1] == "backup"
        # Timestamp portion: YYYYMMDDTHHMMSSz
        ts = parts[2]
        assert "T" in ts
        assert ts.endswith("Z")
        # Should be parseable
        datetime.strptime(ts, "%Y%m%dT%H%M%SZ")

    @patch(
        "gitre.rewriter.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "git branch"),
    )
    def test_raises_on_failure(self, mock_run: MagicMock) -> None:
        """Should propagate CalledProcessError if git branch fails."""
        with pytest.raises(subprocess.CalledProcessError):
            create_backup("/fake/repo")

    def test_create_backup_in_real_repo(self, tmp_git_repo: Path) -> None:
        """Use tmp_git_repo fixture: backup branch should exist after creation."""
        branch_name = create_backup(str(tmp_git_repo))
        assert branch_name.startswith("gitre-backup-")

        # Verify the branch actually exists in the repo
        result = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=str(tmp_git_repo),
            capture_output=True,
            text=True,
        )
        assert branch_name in result.stdout

    def test_create_backup_real_repo_points_to_head(self, tmp_git_repo: Path) -> None:
        """Backup branch should point to the same commit as HEAD."""
        branch_name = create_backup(str(tmp_git_repo))

        # Get HEAD commit
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(tmp_git_repo),
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Get backup branch commit
        backup_commit = subprocess.run(
            ["git", "rev-parse", branch_name],
            cwd=str(tmp_git_repo),
            capture_output=True,
            text=True,
        ).stdout.strip()

        assert head == backup_commit


# ===========================================================================
# 3. build_message_callback — generates valid Python callback code
# ===========================================================================


class TestBuildMessageCallback:
    """Tests for build_message_callback() and _build_commit_callback()."""

    def test_returns_string(self) -> None:
        """build_message_callback should always return a string."""
        msgs = [_make_msg()]
        result = build_message_callback(msgs)
        assert isinstance(result, str)

    def test_identity_callback(self) -> None:
        """Standalone build_message_callback returns an identity callback."""
        result = build_message_callback([_make_msg()])
        assert "return message" in result

    def test_empty_messages_produces_identity(self) -> None:
        """Empty list should also produce a valid identity callback."""
        result = build_message_callback([])
        assert isinstance(result, str)
        assert "return message" in result

    def test_commit_callback_contains_hash_map(self) -> None:
        """_build_commit_callback should produce HASH_MAP dict."""
        hash_map = {"abc123": "new message"}
        script = _build_commit_callback(hash_map)
        assert "HASH_MAP" in script

    def test_commit_callback_has_hash_and_message(self) -> None:
        """Generated callback should contain both hash and new message."""
        hash_map = {"abc123def456": "docs: fix typo in README"}
        script = _build_commit_callback(hash_map)
        assert "'abc123def456'" in script
        assert "'docs: fix typo in README'" in script

    def test_commit_callback_uses_original_id(self) -> None:
        """Callback should use commit.original_id for matching."""
        hash_map = {"abc123": "new msg"}
        script = _build_commit_callback(hash_map)
        assert "original_id" in script
        assert "encode('utf-8')" in script

    def test_commit_callback_with_multiple_entries(self) -> None:
        """Should handle multiple hash -> message mappings."""
        hash_map = {
            "aaa111": "rewritten one",
            "bbb222": "rewritten two",
            "ccc333": "rewritten three",
        }
        script = _build_commit_callback(hash_map)
        for h, msg in hash_map.items():
            assert repr(h) in script
            assert repr(msg) in script

    def test_commit_callback_is_valid_python(self) -> None:
        """The generated callback should be compilable Python."""
        hash_map = {"abc123": "feat: new commit msg"}
        script = _build_commit_callback(hash_map)
        # Should compile without errors (as top-level code, not a function)
        compile(script, "<test>", "exec")


# ===========================================================================
# 4. save_remotes / restore_remotes
# ===========================================================================


class TestSaveRemotes:
    """Tests for save_remotes()."""

    @patch("gitre.rewriter.subprocess.run")
    def test_parses_fetch_urls(self, mock_run: MagicMock) -> None:
        """Should parse origin fetch URL from git remote -v output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "origin\thttps://github.com/user/repo.git (fetch)\n"
                "origin\thttps://github.com/user/repo.git (push)\n"
            ),
        )
        result = save_remotes("/fake/repo")
        assert result == {"origin": "https://github.com/user/repo.git"}

    @patch("gitre.rewriter.subprocess.run")
    def test_multiple_remotes(self, mock_run: MagicMock) -> None:
        """Should handle multiple remotes."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "origin\thttps://github.com/user/repo.git (fetch)\n"
                "origin\thttps://github.com/user/repo.git (push)\n"
                "upstream\thttps://github.com/upstream/repo.git (fetch)\n"
                "upstream\thttps://github.com/upstream/repo.git (push)\n"
            ),
        )
        result = save_remotes("/fake/repo")
        assert result == {
            "origin": "https://github.com/user/repo.git",
            "upstream": "https://github.com/upstream/repo.git",
        }

    @patch("gitre.rewriter.subprocess.run")
    def test_no_remotes(self, mock_run: MagicMock) -> None:
        """Should return empty dict when no remotes configured."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = save_remotes("/fake/repo")
        assert result == {}


class TestRestoreRemotes:
    """Tests for restore_remotes()."""

    @patch("gitre.rewriter.subprocess.run")
    def test_adds_remotes(self, mock_run: MagicMock) -> None:
        """Should call git remote add for each remote and set upstream."""
        branch_result = MagicMock(returncode=0)
        branch_result.stdout = "master\n"
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git remote add
            branch_result,            # git branch --show-current
            MagicMock(returncode=0),  # git branch --set-upstream-to
        ]
        restore_remotes("/fake/repo", {"origin": "https://example.com/repo.git"})
        assert mock_run.call_args_list[0][0][0] == [
            "git", "remote", "add", "origin", "https://example.com/repo.git",
        ]
        assert mock_run.call_args_list[2][0][0] == [
            "git", "branch", "--set-upstream-to", "origin/master",
        ]

    @patch("gitre.rewriter.subprocess.run")
    def test_empty_dict_is_noop(self, mock_run: MagicMock) -> None:
        """Should not call git remote add when no remotes to restore."""
        branch_result = MagicMock(returncode=0)
        branch_result.stdout = "master\n"
        mock_run.return_value = branch_result
        restore_remotes("/fake/repo", {})
        for call in mock_run.call_args_list:
            args = call[0][0]
            assert args[0:3] != ["git", "remote", "add"]


# ===========================================================================
# 4b. commit_artifacts / force_push
# ===========================================================================


class TestCommitArtifacts:
    """Tests for commit_artifacts()."""

    @patch("gitre.rewriter.subprocess.run")
    def test_stages_and_commits(self, mock_run: MagicMock) -> None:
        """Should stage .gitre/ and commit."""
        # git add succeeds, git diff --cached returns 1 (changes staged), git commit succeeds
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git add
            MagicMock(returncode=1),  # git diff --cached --quiet (changes exist)
            MagicMock(returncode=0),  # git commit
        ]
        commit_artifacts("/fake/repo")
        assert mock_run.call_count == 3
        # First call is git add
        assert ".gitre/" in mock_run.call_args_list[0][0][0]

    @patch("gitre.rewriter.subprocess.run")
    def test_includes_changelog(self, mock_run: MagicMock) -> None:
        """Should stage changelog file when provided."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git add
            MagicMock(returncode=1),  # git diff --cached --quiet
            MagicMock(returncode=0),  # git commit
        ]
        commit_artifacts("/fake/repo", changelog_file="CHANGELOG.md")
        add_args = mock_run.call_args_list[0][0][0]
        assert "CHANGELOG.md" in add_args

    @patch("gitre.rewriter.subprocess.run")
    def test_noop_when_nothing_staged(self, mock_run: MagicMock) -> None:
        """Should skip commit when nothing is staged."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git add
            MagicMock(returncode=0),  # git diff --cached --quiet (nothing staged)
        ]
        commit_artifacts("/fake/repo")
        assert mock_run.call_count == 2  # No commit call


class TestForcePush:
    """Tests for force_push()."""

    @patch("gitre.rewriter.subprocess.run")
    def test_pushes_to_remote(self, mock_run: MagicMock) -> None:
        """Should force push current branch to first remote."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="master\n"),  # rev-parse --abbrev-ref HEAD
            MagicMock(returncode=0, stdout="origin\n"),  # git remote
            MagicMock(returncode=0),  # git push --force
        ]
        force_push("/fake/repo")
        push_args = mock_run.call_args_list[2][0][0]
        assert push_args == ["git", "push", "--force", "origin", "master"]

    @patch("gitre.rewriter.subprocess.run")
    def test_no_remotes_raises(self, mock_run: MagicMock) -> None:
        """Should raise RuntimeError when no remotes configured."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="master\n"),  # rev-parse
            MagicMock(returncode=0, stdout=""),  # git remote (empty)
        ]
        with pytest.raises(RuntimeError, match="No remotes configured"):
            force_push("/fake/repo")


# ===========================================================================
# 5. write_changelog — writes content to the correct file path
# ===========================================================================


class TestWriteChangelog:
    """Tests for write_changelog()."""

    def test_writes_to_relative_path(self, tmp_path: Path) -> None:
        """Should write content to a file relative to repo_path."""
        content = "# Changelog\n\n## [1.0.0]\n- Added stuff"
        write_changelog(str(tmp_path), content, "CHANGELOG.md")
        target = tmp_path / "CHANGELOG.md"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == content

    def test_writes_to_absolute_path(self, tmp_path: Path) -> None:
        """Should write content to an absolute path, ignoring repo_path for joining."""
        target = tmp_path / "output" / "CHANGELOG.md"
        content = "# Changes"
        write_changelog(str(tmp_path), content, str(target))
        assert target.exists()
        assert target.read_text(encoding="utf-8") == content

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Should create intermediate directories if they don't exist."""
        content = "log"
        write_changelog(str(tmp_path), content, "deep/nested/CHANGELOG.md")
        target = tmp_path / "deep" / "nested" / "CHANGELOG.md"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == content

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        """Should overwrite a previously existing file."""
        target = tmp_path / "CHANGELOG.md"
        target.write_text("old content", encoding="utf-8")
        write_changelog(str(tmp_path), "new content", "CHANGELOG.md")
        assert target.read_text(encoding="utf-8") == "new content"

    def test_writes_to_real_git_repo(self, tmp_git_repo: Path) -> None:
        """Use tmp_git_repo fixture: write changelog into an actual repo."""
        content = "# Changelog\n\n## [0.2.0]\n- Updated stuff\n"
        write_changelog(str(tmp_git_repo), content, "CHANGELOG.md")
        target = tmp_git_repo / "CHANGELOG.md"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == content

    def test_writes_utf8_content(self, tmp_path: Path) -> None:
        """Should handle unicode content correctly."""
        content = (
            "# Changelog\n\n"
            "- Fixed encoding bug \u2014 special chars: \u00e9\u00e0\u00fc\u00f1"
        )
        write_changelog(str(tmp_path), content, "CHANGELOG.md")
        target = tmp_path / "CHANGELOG.md"
        assert target.read_text(encoding="utf-8") == content

    def test_writes_empty_content(self, tmp_path: Path) -> None:
        """Should create the file even with empty string content."""
        write_changelog(str(tmp_path), "", "CHANGELOG.md")
        target = tmp_path / "CHANGELOG.md"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == ""


# ===========================================================================
# 6. confirm_rewrite — works with mocked typer.confirm
# ===========================================================================


class TestConfirmRewrite:
    """Tests for confirm_rewrite()."""

    @patch("gitre.rewriter.typer.confirm", return_value=True)
    def test_returns_true_on_confirm(self, mock_confirm: MagicMock) -> None:
        """Should return True when user confirms."""
        assert confirm_rewrite() is True
        mock_confirm.assert_called_once()

    @patch("gitre.rewriter.typer.confirm", return_value=False)
    def test_returns_false_on_deny(self, mock_confirm: MagicMock) -> None:
        """Should return False when user declines."""
        assert confirm_rewrite() is False
        mock_confirm.assert_called_once()

    @patch("gitre.rewriter.typer.confirm")
    def test_prompt_mentions_rewrite(self, mock_confirm: MagicMock) -> None:
        """The confirmation prompt should mention history rewriting."""
        mock_confirm.return_value = False
        confirm_rewrite()
        prompt_text = mock_confirm.call_args[0][0]
        assert "rewrite" in prompt_text.lower()

    @patch("gitre.rewriter.typer.confirm")
    def test_default_is_false(self, mock_confirm: MagicMock) -> None:
        """Default answer should be False (safe default)."""
        mock_confirm.return_value = False
        confirm_rewrite()
        _, kwargs = mock_confirm.call_args
        assert kwargs.get("default") is False

    @patch("gitre.rewriter.typer.confirm", side_effect=typer.Abort)
    def test_abort_raises(self, mock_confirm: MagicMock) -> None:
        """If the user aborts (Ctrl+C), typer.Abort should propagate."""
        with pytest.raises(typer.Abort):
            confirm_rewrite()


# ===========================================================================
# Integration-style tests using tmp_git_repo
# ===========================================================================


class TestCreateBackupIntegration:
    """Integration tests for create_backup using real git repos."""

    def test_multiple_backups_have_unique_names(self, tmp_git_repo: Path) -> None:
        """Calling create_backup twice should produce different branch names."""
        import time

        name1 = create_backup(str(tmp_git_repo))
        # Sleep briefly to ensure timestamp differs
        time.sleep(1.1)
        name2 = create_backup(str(tmp_git_repo))
        assert name1 != name2

    def test_backup_doesnt_switch_current_branch(self, tmp_git_repo: Path) -> None:
        """Creating a backup branch should not switch the active branch."""
        # Get current branch before
        before = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(tmp_git_repo),
            capture_output=True,
            text=True,
        ).stdout.strip()

        create_backup(str(tmp_git_repo))

        # Get current branch after
        after = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(tmp_git_repo),
            capture_output=True,
            text=True,
        ).stdout.strip()

        assert before == after


# ===========================================================================
# rewrite_history — mocked tests
# ===========================================================================


class TestRewriteHistory:
    """Tests for rewrite_history() with mocked subprocess."""

    @patch("gitre.rewriter.check_filter_repo", return_value=False)
    def test_raises_when_filter_repo_missing(self, _mock: MagicMock) -> None:
        """Should raise RuntimeError with install instructions."""
        with pytest.raises(RuntimeError, match="git-filter-repo is not installed"):
            rewrite_history("/fake/repo", [_make_msg()])

    @patch("gitre.rewriter.subprocess.run")
    @patch("gitre.rewriter.create_backup", return_value="gitre-backup-test")
    @patch("gitre.rewriter.check_filter_repo", return_value=True)
    @patch("gitre.rewriter.save_remotes", return_value={})
    def test_successful_rewrite(
        self,
        _save_remotes: MagicMock,
        _check: MagicMock,
        _backup: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Should return a mapping of short_hash -> subject."""
        filter_result = MagicMock(returncode=0)
        mock_run.return_value = filter_result

        msg = _make_msg(subject="fix: resolve null pointer")
        results = rewrite_history("/fake/repo", [msg])

        assert isinstance(results, dict)
        assert msg.short_hash in results
        assert "fix: resolve null pointer" in results[msg.short_hash]
        _backup.assert_called_once_with("/fake/repo")

    @patch("gitre.rewriter.subprocess.run")
    @patch("gitre.rewriter.create_backup", return_value="gitre-backup-test")
    @patch("gitre.rewriter.check_filter_repo", return_value=True)
    @patch("gitre.rewriter.save_remotes", return_value={"origin": "https://example.com"})
    @patch("gitre.rewriter.restore_remotes")
    def test_rewrite_saves_and_restores_remotes(
        self,
        mock_restore: MagicMock,
        _save: MagicMock,
        _check: MagicMock,
        _backup: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Should save remotes before and restore after rewrite."""
        mock_run.return_value = MagicMock(returncode=0)

        msg = _make_msg()
        rewrite_history("/fake/repo", [msg])

        _save.assert_called_once_with("/fake/repo")
        mock_restore.assert_called_once_with("/fake/repo", {"origin": "https://example.com"})

    @patch("gitre.rewriter.subprocess.run")
    @patch("gitre.rewriter.create_backup", return_value="gitre-backup-test")
    @patch("gitre.rewriter.check_filter_repo", return_value=True)
    @patch("gitre.rewriter.save_remotes", return_value={})
    def test_rewrite_multiple_messages(
        self,
        _save: MagicMock,
        _check: MagicMock,
        _backup: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Should handle multiple messages."""
        mock_run.return_value = MagicMock(returncode=0)

        msg1 = _make_msg(hash="aaa111", short_hash="aaa111")
        msg2 = _make_msg(
            hash="bbb222",
            short_hash="bbb222",
            subject="feat: new thing",
            changelog_category="Added",
            changelog_entry="Added new thing",
        )

        results = rewrite_history("/fake/repo", [msg1, msg2])
        assert len(results) == 2
        assert "aaa111" in results
        assert "bbb222" in results


# ===========================================================================
# rewrite_history with git-filter-repo (real integration)
# ===========================================================================


@requires_filter_repo
class TestRewriteHistoryIntegration:
    """Integration tests that require git-filter-repo to be installed."""

    def test_rewrite_single_commit_message(self, tmp_git_repo: Path) -> None:
        """Should actually rewrite a commit message in a real repo."""
        # Get the latest commit hash
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H %h %s"],
            cwd=str(tmp_git_repo),
            capture_output=True,
            text=True,
        )
        parts = result.stdout.strip().split(" ", 2)
        full_hash = parts[0]
        short_hash = parts[1]

        msg = _make_msg(
            hash=full_hash,
            short_hash=short_hash,
            subject="chore: merge feature branch",
        )

        results = rewrite_history(str(tmp_git_repo), [msg])
        assert short_hash in results

        # Verify the commit message was actually changed
        new_result = subprocess.run(
            ["git", "log", "--all", "--format=%s"],
            cwd=str(tmp_git_repo),
            capture_output=True,
            text=True,
        )
        assert "chore: merge feature branch" in new_result.stdout


# ===========================================================================
# display_proposals — smoke tests
# ===========================================================================


class TestDisplayProposals:
    """Tests for display_proposals()."""

    @patch("gitre.rewriter._console")
    def test_no_proposals(self, mock_console: MagicMock) -> None:
        """Should print a 'no proposals' message for empty list."""
        display_proposals([])
        mock_console.print.assert_called()
        call_str = str(mock_console.print.call_args_list[0])
        assert "No proposals" in call_str

    @patch("gitre.rewriter._console")
    def test_with_proposals_no_commits(self, mock_console: MagicMock) -> None:
        """Should render table + summary without crashing."""
        msgs = [_make_msg()]
        display_proposals(msgs)
        assert mock_console.print.call_count >= 2

    @patch("gitre.rewriter._console")
    def test_with_proposals_and_commits(self, mock_console: MagicMock) -> None:
        """Should render table with original column when commits provided."""
        msgs = [_make_msg()]
        commits = [_make_commit()]
        display_proposals(msgs, commits=commits)
        assert mock_console.print.call_count >= 2

    @patch("gitre.rewriter._console")
    def test_multiple_categories_in_summary(self, mock_console: MagicMock) -> None:
        """Should handle multiple changelog categories without error."""
        msgs = [
            _make_msg(
                hash="aaa",
                short_hash="aaa",
                changelog_category="Added",
                changelog_entry="x",
                subject="feat: something",
            ),
            _make_msg(
                hash="bbb",
                short_hash="bbb",
                changelog_category="Fixed",
                changelog_entry="y",
            ),
        ]
        display_proposals(msgs)
        assert mock_console.print.call_count >= 2
