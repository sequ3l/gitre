"""Tests for gitre.labeler — staged diff analysis and label generation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from gitre.labeler import _build_label_prompt, generate_label, get_staged_diff

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stage_change(repo: Path, filename: str, content: str) -> None:
    """Write *content* to *filename* in *repo* and ``git add`` it."""
    path = repo / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    import subprocess

    subprocess.run(
        ["git", "add", filename],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# get_staged_diff
# ---------------------------------------------------------------------------


class TestGetStagedDiff:
    """Tests for get_staged_diff()."""

    def test_returns_diff_for_staged_file(self, tmp_git_repo: Path) -> None:
        """Should return non-empty stat and patch for staged changes."""
        _stage_change(tmp_git_repo, "new_file.py", "print('hello')\n")
        stat, patch_text = get_staged_diff(str(tmp_git_repo))
        assert "new_file.py" in stat
        assert "print('hello')" in patch_text

    def test_returns_empty_when_nothing_staged(
        self, tmp_git_repo: Path,
    ) -> None:
        """Should return empty strings when nothing is staged."""
        stat, patch_text = get_staged_diff(str(tmp_git_repo))
        assert stat == ""
        assert patch_text == ""

    def test_multiple_staged_files(self, tmp_git_repo: Path) -> None:
        """Should include all staged files in the diff."""
        _stage_change(tmp_git_repo, "a.py", "a = 1\n")
        _stage_change(tmp_git_repo, "b.py", "b = 2\n")
        stat, patch_text = get_staged_diff(str(tmp_git_repo))
        assert "a.py" in stat
        assert "b.py" in stat
        assert "a = 1" in patch_text
        assert "b = 2" in patch_text


# ---------------------------------------------------------------------------
# _build_label_prompt
# ---------------------------------------------------------------------------


class TestBuildLabelPrompt:
    """Tests for _build_label_prompt()."""

    def test_includes_diff_stat(self) -> None:
        prompt = _build_label_prompt("1 file changed", "diff content")
        assert "1 file changed" in prompt

    def test_includes_diff_patch(self) -> None:
        prompt = _build_label_prompt("stat", "+new line added")
        assert "+new line added" in prompt

    def test_requests_json_response(self) -> None:
        prompt = _build_label_prompt("stat", "patch")
        assert '"subject"' in prompt
        assert '"changelog_category"' in prompt

    def test_truncates_large_diffs(self) -> None:
        large_patch = "x" * 300_000
        prompt = _build_label_prompt("stat", large_patch)
        assert "[... diff truncated for size ...]" in prompt
        assert len(prompt) < 300_000


# ---------------------------------------------------------------------------
# generate_label
# ---------------------------------------------------------------------------


class TestGenerateLabel:
    """Tests for generate_label() with mocked Claude SDK."""

    @pytest.mark.asyncio
    async def test_returns_generated_message(
        self, tmp_git_repo: Path,
    ) -> None:
        """Should produce a GeneratedMessage from staged changes."""
        _stage_change(tmp_git_repo, "feature.py", "def feature(): pass\n")

        mock_response = json.dumps({
            "subject": "Add feature function stub",
            "body": None,
            "changelog_category": "Added",
            "changelog_entry": "Add empty feature() function.",
        })

        with patch(
            "gitre.labeler._call_claude",
            new_callable=AsyncMock,
            return_value=(mock_response, 100, 0.01),
        ):
            msg = await generate_label(str(tmp_git_repo), model="opus")

        assert msg.subject == "Add feature function stub"
        assert msg.changelog_category == "Added"
        assert msg.hash == "staged"
        assert msg.short_hash == "staged"

    @pytest.mark.asyncio
    async def test_raises_when_nothing_staged(
        self, tmp_git_repo: Path,
    ) -> None:
        """Should raise RuntimeError when staging area is empty."""
        with pytest.raises(RuntimeError, match="No staged changes"):
            await generate_label(str(tmp_git_repo), model="opus")

    @pytest.mark.asyncio
    async def test_handles_markdown_fenced_response(
        self, tmp_git_repo: Path,
    ) -> None:
        """Should handle Claude wrapping JSON in markdown fences."""
        _stage_change(tmp_git_repo, "fix.py", "x = 1\n")

        fenced = (
            "```json\n"
            + json.dumps({
                "subject": "Fix variable assignment",
                "body": None,
                "changelog_category": "Fixed",
                "changelog_entry": "Fix variable assignment in fix.py.",
            })
            + "\n```"
        )

        with patch(
            "gitre.labeler._call_claude",
            new_callable=AsyncMock,
            return_value=(fenced, 50, 0.005),
        ):
            msg = await generate_label(str(tmp_git_repo), model="opus")

        assert msg.subject == "Fix variable assignment"
        assert msg.changelog_category == "Fixed"
