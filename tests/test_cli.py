"""Tests for the gitre.cli module (Typer CLI entry point).

All external calls (git subprocess, analyzer, generator, cache, rewriter,
formatter) are mocked to ensure tests are fast, deterministic, and never
touch real repositories or the Claude SDK.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.exceptions import Exit as ClickExit
from typer.testing import CliRunner

from gitre.cli import OutputFormat, _build_tags_dict, _validate_git_repo, app
from gitre.models import AnalysisResult, CommitInfo, GeneratedMessage

runner = CliRunner()


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_commit() -> CommitInfo:
    """A minimal CommitInfo for CLI tests."""
    return CommitInfo(
        hash="aaa1111111111111111111111111111111111111",
        short_hash="aaa1111",
        author="CLI Tester <cli@test.com>",
        date=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
        original_message="wip",
        diff_stat="1 file changed",
        diff_patch="diff --git a/f.py b/f.py",
        files_changed=1,
        insertions=5,
        deletions=2,
        tags=["v1.0.0"],
    )


@pytest.fixture()
def fake_message() -> GeneratedMessage:
    """A minimal GeneratedMessage for CLI tests."""
    return GeneratedMessage(
        hash="aaa1111111111111111111111111111111111111",
        short_hash="aaa1111",
        subject="Add feature X",
        body="Extended description here",
        changelog_category="Added",
        changelog_entry="Feature X was added",
    )


@pytest.fixture()
def fake_result(fake_message: GeneratedMessage) -> AnalysisResult:
    """A minimal cached AnalysisResult."""
    return AnalysisResult(
        repo_path="/fake/repo",
        head_hash="aaa1111111111111111111111111111111111111",
        from_ref=None,
        to_ref=None,
        commits_analyzed=1,
        messages=[fake_message],
        tags={"aaa1111111111111111111111111111111111111": "v1.0.0"},
    )


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------


class TestOutputFormatEnum:
    """Verify the OutputFormat enum values."""

    def test_members(self) -> None:
        assert OutputFormat.changelog.value == "changelog"
        assert OutputFormat.messages.value == "messages"
        assert OutputFormat.both.value == "both"


class TestBuildTagsDict:
    """Tests for _build_tags_dict helper."""

    def test_empty_commits(self) -> None:
        assert _build_tags_dict([]) == {}

    def test_commits_without_tags(self, fake_commit: CommitInfo) -> None:
        untagged = fake_commit.model_copy(update={"tags": []})
        assert _build_tags_dict([untagged]) == {}

    def test_commits_with_tags(self, fake_commit: CommitInfo) -> None:
        tags = _build_tags_dict([fake_commit])
        assert tags[fake_commit.hash] == "v1.0.0"


class TestValidateGitRepo:
    """Tests for _validate_git_repo helper."""

    def test_nonexistent_path(self, tmp_path: Path) -> None:
        bad_path = str(tmp_path / "does-not-exist")
        with pytest.raises(ClickExit):
            _validate_git_repo(bad_path)

    def test_file_not_dir(self, tmp_path: Path) -> None:
        file = tmp_path / "file.txt"
        file.write_text("hello")
        with pytest.raises(ClickExit):
            _validate_git_repo(str(file))

    def test_not_a_git_repo(self, tmp_path: Path) -> None:
        with patch("gitre.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128)
            with pytest.raises(ClickExit):
                _validate_git_repo(str(tmp_path))

    def test_git_not_installed(self, tmp_path: Path) -> None:
        with patch("gitre.cli.subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(ClickExit):
                _validate_git_repo(str(tmp_path))


# ---------------------------------------------------------------------------
# Analyze command tests
# ---------------------------------------------------------------------------


class TestAnalyzeCommand:
    """Tests for the 'analyze' command."""

    def test_analyze_missing_repo_path(self) -> None:
        """analyze requires a repo_path argument."""
        result = runner.invoke(app, ["analyze"])
        assert result.exit_code != 0

    def test_analyze_invalid_repo_path(self, tmp_path: Path) -> None:
        """'gitre analyze' with an invalid (non-existent) repo_path shows error."""
        bad_path = str(tmp_path / "does-not-exist")
        result = runner.invoke(app, ["analyze", bad_path])
        assert result.exit_code != 0
        assert "Error" in result.output or "error" in result.output

    def test_analyze_not_a_git_repo(self, tmp_path: Path) -> None:
        """'gitre analyze' with a valid dir that is not a git repo shows error."""
        with patch("gitre.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128)
            result = runner.invoke(app, ["analyze", str(tmp_path)])
        assert result.exit_code != 0
        assert "not a git repository" in result.output

    @patch("gitre.cli.cache.save_analysis")
    @patch("gitre.cli._get_head_hash", return_value="abcdef1234567890")
    @patch("gitre.cli.asyncio.run")
    @patch("gitre.cli.analyzer.enrich_commit")
    @patch("gitre.cli.analyzer.get_commits")
    @patch("gitre.cli._validate_git_repo")
    def test_analyze_happy_path(
        self,
        mock_validate: MagicMock,
        mock_get_commits: MagicMock,
        mock_enrich: MagicMock,
        mock_asyncio_run: MagicMock,
        mock_head: MagicMock,
        mock_save: MagicMock,
        fake_commit: CommitInfo,
        fake_message: GeneratedMessage,
    ) -> None:
        """Successful analyze: get commits, enrich, generate, cache, output."""
        mock_get_commits.return_value = [fake_commit]
        mock_enrich.return_value = fake_commit
        mock_asyncio_run.return_value = [fake_message]

        result = runner.invoke(app, ["analyze", "/fake/repo"])

        assert result.exit_code == 0
        mock_validate.assert_called_once_with("/fake/repo")
        mock_get_commits.assert_called_once()
        mock_enrich.assert_called_once()
        mock_asyncio_run.assert_called_once()
        mock_save.assert_called_once()

    @patch("gitre.cli.formatter.format_both", return_value="FORMATTED_OUTPUT")
    @patch("gitre.cli.cache.save_analysis")
    @patch("gitre.cli._get_head_hash", return_value="abcdef1234567890")
    @patch("gitre.cli.asyncio.run")
    @patch("gitre.cli.analyzer.enrich_commit")
    @patch("gitre.cli.analyzer.get_commits")
    @patch("gitre.cli._validate_git_repo")
    def test_analyze_full_flow_mocks_all_deps(
        self,
        mock_validate: MagicMock,
        mock_get_commits: MagicMock,
        mock_enrich: MagicMock,
        mock_asyncio_run: MagicMock,
        mock_head: MagicMock,
        mock_save: MagicMock,
        mock_format: MagicMock,
        fake_commit: CommitInfo,
        fake_message: GeneratedMessage,
    ) -> None:
        """Full analyze flow verifies analyzer, generator, cache, and formatter are all called."""
        mock_get_commits.return_value = [fake_commit]
        mock_enrich.return_value = fake_commit
        mock_asyncio_run.return_value = [fake_message]

        result = runner.invoke(app, ["analyze", "/fake/repo"])

        assert result.exit_code == 0
        # Analyzer: commits fetched and enriched
        mock_get_commits.assert_called_once()
        mock_enrich.assert_called_once_with("/fake/repo", fake_commit)
        # Generator: messages generated via asyncio.run
        mock_asyncio_run.assert_called_once()
        # Cache: analysis saved
        mock_save.assert_called_once()
        saved_result = mock_save.call_args[0][1]
        assert isinstance(saved_result, AnalysisResult)
        assert saved_result.messages == [fake_message]
        # Formatter: output formatted (default is 'both')
        mock_format.assert_called_once()
        assert "FORMATTED_OUTPUT" in result.output

    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.analyzer.get_commits")
    def test_analyze_no_commits(
        self,
        mock_get_commits: MagicMock,
        mock_validate: MagicMock,
    ) -> None:
        """Analyze exits cleanly when no commits are found."""
        mock_get_commits.return_value = []

        result = runner.invoke(app, ["analyze", "/fake/repo"])

        assert result.exit_code == 0
        assert "No commits found" in result.output

    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.analyzer.get_commits", side_effect=Exception("git error"))
    def test_analyze_get_commits_error(
        self,
        mock_get_commits: MagicMock,
        mock_validate: MagicMock,
    ) -> None:
        """Analyze handles errors when fetching commits."""
        import subprocess as sp

        mock_get_commits.side_effect = sp.CalledProcessError(1, "git")

        result = runner.invoke(app, ["analyze", "/fake/repo"])

        assert result.exit_code == 1

    @patch("gitre.cli.cache.save_analysis")
    @patch("gitre.cli._get_head_hash", return_value="abcdef")
    @patch("gitre.cli.asyncio.run")
    @patch("gitre.cli.analyzer.enrich_commit")
    @patch("gitre.cli.analyzer.get_commits")
    @patch("gitre.cli._validate_git_repo")
    def test_analyze_generation_error(
        self,
        mock_validate: MagicMock,
        mock_get_commits: MagicMock,
        mock_enrich: MagicMock,
        mock_asyncio_run: MagicMock,
        mock_head: MagicMock,
        mock_save: MagicMock,
        fake_commit: CommitInfo,
    ) -> None:
        """Analyze handles RuntimeError from generation."""
        mock_get_commits.return_value = [fake_commit]
        mock_enrich.return_value = fake_commit
        mock_asyncio_run.side_effect = RuntimeError("SDK not installed")

        result = runner.invoke(app, ["analyze", "/fake/repo"])

        assert result.exit_code == 1
        assert "Error generating messages" in result.output

    @patch("gitre.cli.cache.save_analysis")
    @patch("gitre.cli._get_head_hash", return_value="abcdef")
    @patch("gitre.cli.asyncio.run")
    @patch("gitre.cli.analyzer.enrich_commit")
    @patch("gitre.cli.analyzer.get_commits")
    @patch("gitre.cli._validate_git_repo")
    def test_analyze_with_output_changelog(
        self,
        mock_validate: MagicMock,
        mock_get_commits: MagicMock,
        mock_enrich: MagicMock,
        mock_asyncio_run: MagicMock,
        mock_head: MagicMock,
        mock_save: MagicMock,
        fake_commit: CommitInfo,
        fake_message: GeneratedMessage,
    ) -> None:
        """--output changelog produces changelog output."""
        mock_get_commits.return_value = [fake_commit]
        mock_enrich.return_value = fake_commit
        mock_asyncio_run.return_value = [fake_message]

        result = runner.invoke(app, ["analyze", "/fake/repo", "-o", "changelog"])

        assert result.exit_code == 0
        assert "Changelog" in result.output

    @patch("gitre.cli.cache.save_analysis")
    @patch("gitre.cli._get_head_hash", return_value="abcdef")
    @patch("gitre.cli.asyncio.run")
    @patch("gitre.cli.analyzer.enrich_commit")
    @patch("gitre.cli.analyzer.get_commits")
    @patch("gitre.cli._validate_git_repo")
    def test_analyze_with_output_messages(
        self,
        mock_validate: MagicMock,
        mock_get_commits: MagicMock,
        mock_enrich: MagicMock,
        mock_asyncio_run: MagicMock,
        mock_head: MagicMock,
        mock_save: MagicMock,
        fake_commit: CommitInfo,
        fake_message: GeneratedMessage,
    ) -> None:
        """--output messages produces message output."""
        mock_get_commits.return_value = [fake_commit]
        mock_enrich.return_value = fake_commit
        mock_asyncio_run.return_value = [fake_message]

        result = runner.invoke(app, ["analyze", "/fake/repo", "-o", "messages"])

        assert result.exit_code == 0
        assert "Proposed Commit Messages" in result.output

    @patch("gitre.cli.cache.save_analysis")
    @patch("gitre.cli._get_head_hash", return_value="abcdef")
    @patch("gitre.cli.asyncio.run")
    @patch("gitre.cli.analyzer.enrich_commit")
    @patch("gitre.cli.analyzer.get_commits")
    @patch("gitre.cli._validate_git_repo")
    def test_analyze_writes_output_file(
        self,
        mock_validate: MagicMock,
        mock_get_commits: MagicMock,
        mock_enrich: MagicMock,
        mock_asyncio_run: MagicMock,
        mock_head: MagicMock,
        mock_save: MagicMock,
        fake_commit: CommitInfo,
        fake_message: GeneratedMessage,
        tmp_path: Path,
    ) -> None:
        """--out-file / -f writes output to a file."""
        mock_get_commits.return_value = [fake_commit]
        mock_enrich.return_value = fake_commit
        mock_asyncio_run.return_value = [fake_message]

        out_file = str(tmp_path / "output.md")
        result = runner.invoke(app, ["analyze", "/fake/repo", "-f", out_file])

        assert result.exit_code == 0
        assert Path(out_file).exists()
        content = Path(out_file).read_text(encoding="utf-8")
        assert len(content) > 0

    @patch("gitre.cli.cache.save_analysis")
    @patch("gitre.cli._get_head_hash", return_value="abcdef")
    @patch("gitre.cli.asyncio.run")
    @patch("gitre.cli.analyzer.enrich_commit")
    @patch("gitre.cli.analyzer.get_commits")
    @patch("gitre.cli._validate_git_repo")
    def test_analyze_verbose(
        self,
        mock_validate: MagicMock,
        mock_get_commits: MagicMock,
        mock_enrich: MagicMock,
        mock_asyncio_run: MagicMock,
        mock_head: MagicMock,
        mock_save: MagicMock,
        fake_commit: CommitInfo,
        fake_message: GeneratedMessage,
    ) -> None:
        """--verbose flag produces extra output without errors."""
        mock_get_commits.return_value = [fake_commit]
        mock_enrich.return_value = fake_commit
        mock_asyncio_run.return_value = [fake_message]

        result = runner.invoke(app, ["analyze", "/fake/repo", "-v"])

        assert result.exit_code == 0

    @patch("gitre.cli._run_commit_flow")
    @patch("gitre.cli.cache.save_analysis")
    @patch("gitre.cli._get_head_hash", return_value="abcdef")
    @patch("gitre.cli.asyncio.run")
    @patch("gitre.cli.analyzer.enrich_commit")
    @patch("gitre.cli.analyzer.get_commits")
    @patch("gitre.cli._validate_git_repo")
    def test_analyze_with_live_flag(
        self,
        mock_validate: MagicMock,
        mock_get_commits: MagicMock,
        mock_enrich: MagicMock,
        mock_asyncio_run: MagicMock,
        mock_head: MagicMock,
        mock_save: MagicMock,
        mock_commit_flow: MagicMock,
        fake_commit: CommitInfo,
        fake_message: GeneratedMessage,
    ) -> None:
        """--live triggers the commit flow after analysis."""
        mock_get_commits.return_value = [fake_commit]
        mock_enrich.return_value = fake_commit
        mock_asyncio_run.return_value = [fake_message]

        result = runner.invoke(app, ["analyze", "/fake/repo", "--live"])

        assert result.exit_code == 0
        mock_commit_flow.assert_called_once()

    @patch("gitre.cli.cache.save_analysis")
    @patch("gitre.cli._get_head_hash", return_value="abcdef")
    @patch("gitre.cli.asyncio.run")
    @patch("gitre.cli.analyzer.enrich_commit")
    @patch("gitre.cli.analyzer.get_commits")
    @patch("gitre.cli._validate_git_repo")
    def test_analyze_with_from_to(
        self,
        mock_validate: MagicMock,
        mock_get_commits: MagicMock,
        mock_enrich: MagicMock,
        mock_asyncio_run: MagicMock,
        mock_head: MagicMock,
        mock_save: MagicMock,
        fake_commit: CommitInfo,
        fake_message: GeneratedMessage,
    ) -> None:
        """--from and --to options are passed to get_commits."""
        mock_get_commits.return_value = [fake_commit]
        mock_enrich.return_value = fake_commit
        mock_asyncio_run.return_value = [fake_message]

        result = runner.invoke(
            app, ["analyze", "/fake/repo", "--from", "v0.1.0", "--to", "v0.2.0"]
        )

        assert result.exit_code == 0
        mock_get_commits.assert_called_once_with(
            "/fake/repo", from_ref="v0.1.0", to_ref="v0.2.0"
        )


# ---------------------------------------------------------------------------
# Commit command tests
# ---------------------------------------------------------------------------


class TestCommitCommand:
    """Tests for the 'commit' command."""

    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.cache.load_analysis", side_effect=FileNotFoundError)
    def test_commit_no_cache(
        self,
        mock_load: MagicMock,
        mock_validate: MagicMock,
    ) -> None:
        """commit exits with error when no cached analysis exists."""
        result = runner.invoke(app, ["commit", "/fake/repo"])

        assert result.exit_code == 1
        assert "no cached analysis" in result.output

    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.cache.load_analysis")
    @patch("gitre.cli.cache.validate_cache", return_value=(False, "Cache is stale"))
    @patch("gitre.cli._run_commit_flow")
    def test_commit_stale_cache_warns(
        self,
        mock_flow: MagicMock,
        mock_validate_cache: MagicMock,
        mock_load: MagicMock,
        mock_validate: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """commit warns when cache is stale but continues."""
        mock_load.return_value = fake_result

        result = runner.invoke(app, ["commit", "/fake/repo"])

        assert "Warning" in result.output or "Cache is stale" in result.output

    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.cache.load_analysis")
    @patch("gitre.cli.cache.validate_cache", return_value=(True, ""))
    @patch("gitre.cli._run_commit_flow")
    def test_commit_happy_path(
        self,
        mock_flow: MagicMock,
        mock_validate_cache: MagicMock,
        mock_load: MagicMock,
        mock_validate: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """commit delegates to _run_commit_flow with all messages (no filter)."""
        mock_load.return_value = fake_result

        result = runner.invoke(app, ["commit", "/fake/repo"])

        mock_flow.assert_called_once()
        # Without --only/--skip, all messages should be passed as filtered_messages
        call_kwargs = mock_flow.call_args[1]
        assert "filtered_messages" in call_kwargs
        assert len(call_kwargs["filtered_messages"]) == len(fake_result.messages)

    @patch("gitre.cli.rewriter.commit_artifacts")
    @patch("gitre.cli.rewriter.rewrite_history", return_value={"aaa1111": "'wip' -> 'Add X'"})
    @patch("gitre.cli.rewriter.check_filter_repo", return_value=True)
    @patch("gitre.cli.rewriter.display_proposals")
    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.cache.load_analysis")
    @patch("gitre.cli.cache.validate_cache", return_value=(True, ""))
    def test_commit_end_to_end_with_rewriter(
        self,
        mock_validate_cache: MagicMock,
        mock_load: MagicMock,
        mock_validate: MagicMock,
        mock_display: MagicMock,
        mock_check: MagicMock,
        mock_rewrite: MagicMock,
        mock_artifacts: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """'gitre commit' loads cache and applies rewrite through full flow (mock rewriter)."""
        mock_load.return_value = fake_result

        result = runner.invoke(app, ["commit", "/fake/repo", "-y"])

        assert result.exit_code == 0
        # Cache was loaded
        mock_load.assert_called_once_with("/fake/repo")
        # Cache was validated
        mock_validate_cache.assert_called_once()
        # Proposals were displayed
        mock_display.assert_called_once()
        # git-filter-repo was checked
        mock_check.assert_called_once()
        # History was rewritten
        mock_rewrite.assert_called_once()
        assert mock_rewrite.call_args[0][0] == "/fake/repo"
        # Artifacts were committed after rewrite
        mock_artifacts.assert_called_once()
        # Success message in output
        assert "Successfully rewrote" in result.output

    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.cache.load_analysis")
    @patch("gitre.cli.cache.validate_cache", return_value=(True, ""))
    @patch("gitre.cli._run_commit_flow")
    def test_commit_only_filter(
        self,
        mock_flow: MagicMock,
        mock_validate_cache: MagicMock,
        mock_load: MagicMock,
        mock_validate: MagicMock,
        fake_result: AnalysisResult,
        fake_message: GeneratedMessage,
    ) -> None:
        """--only filters to specified short hashes and passes filtered messages."""
        mock_load.return_value = fake_result

        result = runner.invoke(app, ["commit", "/fake/repo", "--only", "aaa1111"])

        # Should succeed (aaa1111 matches our fake_message)
        mock_flow.assert_called_once()
        # Verify filtered_messages kwarg contains only the matching message
        call_kwargs = mock_flow.call_args[1]
        assert "filtered_messages" in call_kwargs
        assert len(call_kwargs["filtered_messages"]) == 1
        assert call_kwargs["filtered_messages"][0].short_hash == "aaa1111"

    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.cache.load_analysis")
    @patch("gitre.cli.cache.validate_cache", return_value=(True, ""))
    def test_commit_only_filter_no_match(
        self,
        mock_validate_cache: MagicMock,
        mock_load: MagicMock,
        mock_validate: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """--only with non-matching hash results in no commits to rewrite."""
        mock_load.return_value = fake_result

        result = runner.invoke(app, ["commit", "/fake/repo", "--only", "zzz9999"])

        assert result.exit_code == 0
        assert "No commits to rewrite" in result.output

    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.cache.load_analysis")
    @patch("gitre.cli.cache.validate_cache", return_value=(True, ""))
    def test_commit_skip_filter_removes_all(
        self,
        mock_validate_cache: MagicMock,
        mock_load: MagicMock,
        mock_validate: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """--skip that removes all messages results in no commits to rewrite."""
        mock_load.return_value = fake_result

        result = runner.invoke(app, ["commit", "/fake/repo", "--skip", "aaa1111"])

        assert result.exit_code == 0
        assert "No commits to rewrite" in result.output

    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.cache.load_analysis")
    @patch("gitre.cli.cache.validate_cache", return_value=(True, ""))
    @patch("gitre.cli._run_commit_flow")
    def test_commit_skip_filter_passes_remaining(
        self,
        mock_flow: MagicMock,
        mock_validate_cache: MagicMock,
        mock_load: MagicMock,
        mock_validate: MagicMock,
    ) -> None:
        """--skip passes only non-skipped messages as filtered_messages."""
        msg_keep = GeneratedMessage(
            hash="bbb2222222222222222222222222222222222222",
            short_hash="bbb2222",
            subject="Fix typo",
            body=None,
            changelog_category="Fixed",
            changelog_entry="Fixed a typo",
        )
        msg_skip = GeneratedMessage(
            hash="ccc3333333333333333333333333333333333333",
            short_hash="ccc3333",
            subject="Add docs",
            body=None,
            changelog_category="Added",
            changelog_entry="Added docs",
        )
        multi_result = AnalysisResult(
            repo_path="/fake/repo",
            head_hash="bbb2222222222222222222222222222222222222",
            commits_analyzed=2,
            messages=[msg_keep, msg_skip],
        )
        mock_load.return_value = multi_result

        result = runner.invoke(app, ["commit", "/fake/repo", "--skip", "ccc3333"])

        mock_flow.assert_called_once()
        call_kwargs = mock_flow.call_args[1]
        filtered = call_kwargs["filtered_messages"]
        assert len(filtered) == 1
        assert filtered[0].short_hash == "bbb2222"

    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.cache.load_analysis")
    @patch("gitre.cli.cache.validate_cache", return_value=(True, ""))
    @patch("gitre.cli._run_commit_flow")
    def test_commit_default_repo_path(
        self,
        mock_flow: MagicMock,
        mock_validate_cache: MagicMock,
        mock_load: MagicMock,
        mock_validate: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """commit defaults repo_path to '.' when not specified."""
        mock_load.return_value = fake_result

        result = runner.invoke(app, ["commit"])

        mock_validate.assert_called_once_with(".")

    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.cache.load_analysis")
    @patch("gitre.cli.cache.validate_cache", return_value=(True, ""))
    @patch("gitre.cli._run_commit_flow")
    def test_commit_yes_flag_skips_confirmation(
        self,
        mock_flow: MagicMock,
        mock_validate_cache: MagicMock,
        mock_load: MagicMock,
        mock_validate: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """'gitre commit -y' passes yes=True so confirmation is skipped."""
        mock_load.return_value = fake_result

        result = runner.invoke(app, ["commit", "/fake/repo", "-y"])

        assert result.exit_code == 0
        mock_flow.assert_called_once()
        call_kwargs = mock_flow.call_args[1]
        assert call_kwargs["yes"] is True

    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.cache.load_analysis")
    @patch("gitre.cli.cache.validate_cache", return_value=(True, ""))
    @patch("gitre.cli._run_commit_flow")
    def test_commit_yes_long_flag(
        self,
        mock_flow: MagicMock,
        mock_validate_cache: MagicMock,
        mock_load: MagicMock,
        mock_validate: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """'gitre commit --yes' also passes yes=True."""
        mock_load.return_value = fake_result

        result = runner.invoke(app, ["commit", "/fake/repo", "--yes"])

        assert result.exit_code == 0
        mock_flow.assert_called_once()
        call_kwargs = mock_flow.call_args[1]
        assert call_kwargs["yes"] is True

    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.cache.load_analysis")
    @patch("gitre.cli.cache.validate_cache", return_value=(True, ""))
    @patch("gitre.cli._run_commit_flow")
    def test_commit_push_flag(
        self,
        mock_flow: MagicMock,
        mock_validate_cache: MagicMock,
        mock_load: MagicMock,
        mock_validate: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """'gitre commit --push' passes push=True to _run_commit_flow."""
        mock_load.return_value = fake_result

        result = runner.invoke(app, ["commit", "/fake/repo", "--push"])

        assert result.exit_code == 0
        mock_flow.assert_called_once()
        call_kwargs = mock_flow.call_args[1]
        assert call_kwargs["push"] is True

    @patch("gitre.cli._validate_git_repo")
    @patch("gitre.cli.cache.load_analysis", side_effect=ValueError("corrupt"))
    def test_commit_corrupt_cache(
        self,
        mock_load: MagicMock,
        mock_validate: MagicMock,
    ) -> None:
        """commit handles corrupt cache files gracefully."""
        result = runner.invoke(app, ["commit", "/fake/repo"])

        assert result.exit_code == 1
        assert "Error loading cached analysis" in result.output


# ---------------------------------------------------------------------------
# _run_commit_flow tests
# ---------------------------------------------------------------------------


class TestRunCommitFlow:
    """Tests for the _run_commit_flow helper."""

    @patch("gitre.cli.rewriter.commit_artifacts")
    @patch("gitre.cli.rewriter.rewrite_history", return_value={"aaa1111": "'wip' -> 'Add X'"})
    @patch("gitre.cli.rewriter.check_filter_repo", return_value=True)
    @patch("gitre.cli.rewriter.confirm_rewrite", return_value=True)
    @patch("gitre.cli.rewriter.display_proposals")
    def test_happy_path(
        self,
        mock_display: MagicMock,
        mock_confirm: MagicMock,
        mock_check: MagicMock,
        mock_rewrite: MagicMock,
        mock_artifacts: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """Full commit flow: display, confirm, rewrite, commit artifacts."""
        from gitre.cli import _run_commit_flow

        _run_commit_flow("/fake/repo", fake_result, commits=None, yes=False, changelog_file=None)

        mock_display.assert_called_once()
        mock_confirm.assert_called_once()
        mock_rewrite.assert_called_once()
        mock_artifacts.assert_called_once()

    @patch("gitre.cli.rewriter.confirm_rewrite", return_value=False)
    @patch("gitre.cli.rewriter.display_proposals")
    def test_user_aborts(
        self,
        mock_display: MagicMock,
        mock_confirm: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """User declining confirmation aborts the flow."""
        from gitre.cli import _run_commit_flow

        with pytest.raises(ClickExit):
            _run_commit_flow(
                "/fake/repo", fake_result, commits=None, yes=False, changelog_file=None
            )

    @patch("gitre.cli.rewriter.commit_artifacts")
    @patch("gitre.cli.rewriter.rewrite_history", return_value={"aaa1111": "'wip' -> 'Add X'"})
    @patch("gitre.cli.rewriter.check_filter_repo", return_value=True)
    @patch("gitre.cli.rewriter.display_proposals")
    def test_yes_flag_skips_confirm(
        self,
        mock_display: MagicMock,
        mock_check: MagicMock,
        mock_rewrite: MagicMock,
        mock_artifacts: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """--yes / -y skips the confirmation prompt."""
        from gitre.cli import _run_commit_flow

        _run_commit_flow("/fake/repo", fake_result, commits=None, yes=True, changelog_file=None)

        mock_rewrite.assert_called_once()

    @patch("gitre.cli.rewriter.check_filter_repo", return_value=False)
    @patch("gitre.cli.rewriter.get_install_instructions", return_value="pip install ...")
    @patch("gitre.cli.rewriter.display_proposals")
    def test_no_filter_repo(
        self,
        mock_display: MagicMock,
        mock_instructions: MagicMock,
        mock_check: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """Missing git-filter-repo produces an error."""
        from gitre.cli import _run_commit_flow

        with pytest.raises(ClickExit):
            _run_commit_flow(
                "/fake/repo", fake_result, commits=None, yes=True, changelog_file=None
            )

    @patch("gitre.cli.rewriter.commit_artifacts")
    @patch("gitre.cli.formatter.format_changelog", return_value="# Changelog\n")
    @patch("gitre.cli.rewriter.write_changelog")
    @patch("gitre.cli.rewriter.rewrite_history", return_value={"aaa1111": "'wip' -> 'Add X'"})
    @patch("gitre.cli.rewriter.check_filter_repo", return_value=True)
    @patch("gitre.cli.rewriter.display_proposals")
    def test_changelog_file_written(
        self,
        mock_display: MagicMock,
        mock_check: MagicMock,
        mock_rewrite: MagicMock,
        mock_write_cl: MagicMock,
        mock_format_cl: MagicMock,
        mock_artifacts: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """changelog_file triggers changelog formatting and writing."""
        from gitre.cli import _run_commit_flow

        _run_commit_flow(
            "/fake/repo",
            fake_result,
            commits=None,
            yes=True,
            changelog_file="CHANGELOG.md",
        )

        mock_format_cl.assert_called_once()
        mock_write_cl.assert_called_once()

    def test_empty_messages(self, fake_result: AnalysisResult) -> None:
        """No messages means nothing to rewrite."""
        from gitre.cli import _run_commit_flow

        empty_result = fake_result.model_copy(update={"messages": []})
        # Should not raise
        _run_commit_flow(
            "/fake/repo", empty_result, commits=None, yes=True, changelog_file=None
        )

    @patch("gitre.cli.rewriter.check_filter_repo", return_value=True)
    @patch("gitre.cli.rewriter.rewrite_history")
    @patch("gitre.cli.rewriter.display_proposals")
    def test_rewrite_subprocess_error(
        self,
        mock_display: MagicMock,
        mock_rewrite: MagicMock,
        mock_check: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """CalledProcessError during rewrite is caught."""
        import subprocess as sp

        mock_rewrite.side_effect = sp.CalledProcessError(1, "git filter-repo")
        from gitre.cli import _run_commit_flow

        with pytest.raises(ClickExit):
            _run_commit_flow(
                "/fake/repo", fake_result, commits=None, yes=True, changelog_file=None
            )

    @patch("gitre.cli.rewriter.commit_artifacts")
    @patch("gitre.cli.rewriter.rewrite_history", return_value={"aaa1111": "'wip' -> 'Add X'"})
    @patch("gitre.cli.rewriter.check_filter_repo", return_value=True)
    @patch("gitre.cli.rewriter.display_proposals")
    def test_filtered_messages_used_for_rewrite(
        self,
        mock_display: MagicMock,
        mock_check: MagicMock,
        mock_rewrite: MagicMock,
        mock_artifacts: MagicMock,
        fake_result: AnalysisResult,
        fake_message: GeneratedMessage,
    ) -> None:
        """filtered_messages are used for display and rewrite, not result.messages."""
        from gitre.cli import _run_commit_flow

        # Create a subset of messages (simulating --only filter)
        subset = [fake_message]

        # Create a result with MORE messages than our filtered set
        extra_msg = GeneratedMessage(
            hash="bbb2222222222222222222222222222222222222",
            short_hash="bbb2222",
            subject="Fix typo",
            body=None,
            changelog_category="Fixed",
            changelog_entry="Fixed a typo",
        )
        big_result = fake_result.model_copy(
            update={"messages": [fake_message, extra_msg]}
        )

        _run_commit_flow(
            "/fake/repo",
            big_result,
            commits=None,
            yes=True,
            changelog_file=None,
            filtered_messages=subset,
        )

        # Verify only the filtered subset was passed to display and rewrite
        displayed_messages = mock_display.call_args[0][0]
        assert len(displayed_messages) == 1
        assert displayed_messages[0].short_hash == "aaa1111"

        rewritten_messages = mock_rewrite.call_args[0][1]
        assert len(rewritten_messages) == 1
        assert rewritten_messages[0].short_hash == "aaa1111"

    @patch("gitre.cli.rewriter.commit_artifacts")
    @patch("gitre.cli.rewriter.rewrite_history", return_value={"aaa1111": "'wip' -> 'Add X'"})
    @patch("gitre.cli.rewriter.check_filter_repo", return_value=True)
    @patch("gitre.cli.rewriter.display_proposals")
    def test_no_filtered_messages_falls_back_to_result(
        self,
        mock_display: MagicMock,
        mock_check: MagicMock,
        mock_rewrite: MagicMock,
        mock_artifacts: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """Without filtered_messages, all result.messages are used (--live path)."""
        from gitre.cli import _run_commit_flow

        _run_commit_flow(
            "/fake/repo",
            fake_result,
            commits=None,
            yes=True,
            changelog_file=None,
            # No filtered_messages — defaults to None
        )

        displayed_messages = mock_display.call_args[0][0]
        assert len(displayed_messages) == len(fake_result.messages)

    @patch("gitre.cli.rewriter.force_push")
    @patch("gitre.cli.rewriter.commit_artifacts")
    @patch("gitre.cli.rewriter.rewrite_history", return_value={"aaa1111": "'wip' -> 'Add X'"})
    @patch("gitre.cli.rewriter.check_filter_repo", return_value=True)
    @patch("gitre.cli.rewriter.display_proposals")
    def test_push_flag_calls_force_push(
        self,
        mock_display: MagicMock,
        mock_check: MagicMock,
        mock_rewrite: MagicMock,
        mock_artifacts: MagicMock,
        mock_push: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """push=True triggers force_push after rewrite."""
        from gitre.cli import _run_commit_flow

        _run_commit_flow(
            "/fake/repo", fake_result, commits=None, yes=True,
            changelog_file=None, push=True,
        )

        mock_push.assert_called_once_with("/fake/repo")

    @patch("gitre.cli.rewriter.commit_artifacts")
    @patch("gitre.cli.rewriter.rewrite_history", return_value={"aaa1111": "'wip' -> 'Add X'"})
    @patch("gitre.cli.rewriter.check_filter_repo", return_value=True)
    @patch("gitre.cli.rewriter.display_proposals")
    def test_no_push_by_default(
        self,
        mock_display: MagicMock,
        mock_check: MagicMock,
        mock_rewrite: MagicMock,
        mock_artifacts: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """push defaults to False — force_push should not be called."""
        from gitre.cli import _run_commit_flow

        with patch("gitre.cli.rewriter.force_push") as mock_push:
            _run_commit_flow(
                "/fake/repo", fake_result, commits=None, yes=True, changelog_file=None,
            )
            mock_push.assert_not_called()

    @patch("gitre.cli.rewriter.force_push", side_effect=RuntimeError("No remotes"))
    @patch("gitre.cli.rewriter.commit_artifacts")
    @patch("gitre.cli.rewriter.rewrite_history", return_value={"aaa1111": "'wip' -> 'Add X'"})
    @patch("gitre.cli.rewriter.check_filter_repo", return_value=True)
    @patch("gitre.cli.rewriter.display_proposals")
    def test_push_error_exits(
        self,
        mock_display: MagicMock,
        mock_check: MagicMock,
        mock_rewrite: MagicMock,
        mock_artifacts: MagicMock,
        mock_push: MagicMock,
        fake_result: AnalysisResult,
    ) -> None:
        """push=True with a RuntimeError exits with code 1."""
        from gitre.cli import _run_commit_flow

        with pytest.raises(ClickExit):
            _run_commit_flow(
                "/fake/repo", fake_result, commits=None, yes=True,
                changelog_file=None, push=True,
            )


# ---------------------------------------------------------------------------
# _run_generation tests
# ---------------------------------------------------------------------------


class TestRunGeneration:
    """Tests for the _run_generation helper."""

    @patch("gitre.cli.asyncio.run")
    def test_single_generation(
        self,
        mock_asyncio_run: MagicMock,
        fake_commit: CommitInfo,
        fake_message: GeneratedMessage,
    ) -> None:
        """batch_size=1 uses individual generation."""
        from gitre.cli import _run_generation

        mock_asyncio_run.return_value = [fake_message]
        result = _run_generation([fake_commit], "/fake/repo", "sonnet", 1, False)

        assert result == [fake_message]
        mock_asyncio_run.assert_called_once()

    @patch("gitre.cli.asyncio.run")
    def test_batch_generation(
        self,
        mock_asyncio_run: MagicMock,
        fake_commit: CommitInfo,
        fake_message: GeneratedMessage,
    ) -> None:
        """batch_size > 1 uses batch generation."""
        from gitre.cli import _run_generation

        mock_asyncio_run.return_value = [fake_message]
        result = _run_generation([fake_commit], "/fake/repo", "sonnet", 5, False)

        assert result == [fake_message]
        mock_asyncio_run.assert_called_once()


# ---------------------------------------------------------------------------
# _format_output tests
# ---------------------------------------------------------------------------


class TestFormatOutput:
    """Tests for the _format_output helper."""

    def test_changelog_mode(self, fake_message: GeneratedMessage) -> None:
        from gitre.cli import _format_output

        result = _format_output(
            OutputFormat.changelog, [fake_message], [], {}, "keepachangelog"
        )
        assert "Changelog" in result

    def test_messages_mode(self, fake_message: GeneratedMessage) -> None:
        from gitre.cli import _format_output

        result = _format_output(
            OutputFormat.messages, [fake_message], [], {}, "keepachangelog"
        )
        assert "Proposed Commit Messages" in result

    def test_both_mode(self, fake_message: GeneratedMessage) -> None:
        from gitre.cli import _format_output

        result = _format_output(
            OutputFormat.both, [fake_message], [], {}, "keepachangelog"
        )
        assert "Proposed Commit Messages" in result
        assert "Changelog" in result


# ---------------------------------------------------------------------------
# CLI integration / option tests
# ---------------------------------------------------------------------------


class TestCLIOptions:
    """Verify CLI option parsing and defaults."""

    @patch("gitre.cli.cache.save_analysis")
    @patch("gitre.cli._get_head_hash", return_value="abcdef")
    @patch("gitre.cli.asyncio.run")
    @patch("gitre.cli.analyzer.enrich_commit")
    @patch("gitre.cli.analyzer.get_commits")
    @patch("gitre.cli._validate_git_repo")
    def test_batch_size_option(
        self,
        mock_validate: MagicMock,
        mock_get_commits: MagicMock,
        mock_enrich: MagicMock,
        mock_asyncio_run: MagicMock,
        mock_head: MagicMock,
        mock_save: MagicMock,
        fake_commit: CommitInfo,
        fake_message: GeneratedMessage,
    ) -> None:
        """--batch-size option is accepted."""
        mock_get_commits.return_value = [fake_commit]
        mock_enrich.return_value = fake_commit
        mock_asyncio_run.return_value = [fake_message]

        result = runner.invoke(app, ["analyze", "/fake/repo", "--batch-size", "5"])

        assert result.exit_code == 0

    @patch("gitre.cli.cache.save_analysis")
    @patch("gitre.cli._get_head_hash", return_value="abcdef")
    @patch("gitre.cli.asyncio.run")
    @patch("gitre.cli.analyzer.enrich_commit")
    @patch("gitre.cli.analyzer.get_commits")
    @patch("gitre.cli._validate_git_repo")
    def test_model_option(
        self,
        mock_validate: MagicMock,
        mock_get_commits: MagicMock,
        mock_enrich: MagicMock,
        mock_asyncio_run: MagicMock,
        mock_head: MagicMock,
        mock_save: MagicMock,
        fake_commit: CommitInfo,
        fake_message: GeneratedMessage,
    ) -> None:
        """--model option is accepted."""
        mock_get_commits.return_value = [fake_commit]
        mock_enrich.return_value = fake_commit
        mock_asyncio_run.return_value = [fake_message]

        result = runner.invoke(app, ["analyze", "/fake/repo", "--model", "opus"])

        assert result.exit_code == 0

    def test_help_flag(self) -> None:
        """'gitre --help' shows proper usage with both commands listed."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "analyze" in result.output
        assert "commit" in result.output
        # Should mention the app's purpose
        assert "git" in result.output.lower() or "AI" in result.output

    def test_analyze_help(self) -> None:
        """'gitre analyze --help' shows all analyze options."""
        result = runner.invoke(app, ["analyze", "--help"])
        assert result.exit_code == 0
        # All documented options must appear
        for opt in ("--output", "--format", "--from", "--to", "--live",
                     "--out-file", "--model", "--batch-size", "--verbose", "--push"):
            assert opt in result.output, f"Missing option {opt} in analyze help"
        # The positional argument hint
        assert "repo" in result.output.lower()

    def test_commit_help(self) -> None:
        """'gitre commit --help' shows all commit options."""
        result = runner.invoke(app, ["commit", "--help"])
        assert result.exit_code == 0
        for opt in ("--only", "--skip", "--changelog", "--yes", "--push"):
            assert opt in result.output, f"Missing option {opt} in commit help"
