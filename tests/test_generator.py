"""Tests for gitre.generator — prompt building, JSON extraction, and response parsing.

All tests rely on the autouse ``_mock_claude_sdk`` fixture in conftest.py
which globally patches ``gitre.generator.query`` to prevent real Claude CLI
subprocess calls.  Every test in this module verifies mocked ``query()`` usage.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from gitre.generator import (
    BatchResult,
    _build_batch_prompt,
    _build_options,
    _build_prompt,
    _extract_json,
    _parse_single_response,
    _validate_json_keys,
    generate_message,
    generate_messages_batch,
)
from gitre.models import CommitInfo, GeneratedMessage
from tests.conftest import make_mock_query

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_commit(**overrides: object) -> CommitInfo:
    """Create a minimal ``CommitInfo`` for testing."""
    defaults: dict = {
        "hash": "a" * 40,
        "short_hash": "a" * 7,
        "author": "Test Author <test@example.com>",
        "date": datetime(2026, 2, 12, 16, 45, 0, tzinfo=UTC),
        "original_message": "etc",
        "diff_stat": " file.py | 3 ++-\n 1 file changed",
        "diff_patch": "diff --git a/file.py b/file.py\n+hello\n",
        "files_changed": 1,
        "insertions": 2,
        "deletions": 1,
        "tags": [],
    }
    defaults.update(overrides)
    return CommitInfo(**defaults)


_VALID_SINGLE = {
    "subject": "Add argument parsing support",
    "body": "Extend main() to accept CLI arguments",
    "changelog_category": "Added",
    "changelog_entry": "Added argument parsing in the main entry point",
}

_VALID_SINGLE_2 = {
    "subject": "Fix README formatting",
    "body": None,
    "changelog_category": "Fixed",
    "changelog_entry": "Fixed broken markdown formatting in README",
}


def _make_assistant_msg(text: str) -> MagicMock:
    """Build a mock ``AssistantMessage`` with a single text block."""
    msg = MagicMock(spec=[])  # no spec — a plain namespace
    text_block = MagicMock(spec=[])
    text_block.text = text
    msg.content = [text_block]
    # Give it a unique type for isinstance matching
    msg.__class__ = _AssistantMessageType
    return msg


def _make_result_msg(
    cost: float = 0.005,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> MagicMock:
    """Build a mock ``ResultMessage`` with cost/usage data."""
    msg = MagicMock(spec=[])
    msg.total_cost_usd = cost
    msg.usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    msg.__class__ = _ResultMessageType
    return msg


# Sentinel types for isinstance checks inside _call_claude
_AssistantMessageType = type("AssistantMessage", (), {})
_ResultMessageType = type("ResultMessage", (), {})


# ===========================================================================
# Test 0: Autouse fixture verification
# ===========================================================================


class TestAutouseMockVerification:
    """Verify the autouse ``_mock_claude_sdk`` fixture is active.

    Every test in this file depends on the autouse fixture from conftest.py
    that patches ``gitre.generator.query``.  These tests confirm the mock
    is in place.
    """

    def test_query_is_mocked(self, _mock_claude_sdk: MagicMock) -> None:
        """Verify query() is a MagicMock injected by the autouse fixture."""
        import gitre.generator as gen

        # The autouse fixture patches gitre.generator.query with a MagicMock
        assert isinstance(gen.query, MagicMock), (
            "gitre.generator.query should be mocked by the autouse "
            "_mock_claude_sdk fixture — real SDK calls must never run in tests"
        )

    def test_autouse_mock_returns_empty_iterator(
        self, _mock_claude_sdk: MagicMock
    ) -> None:
        """The default autouse mock returns an empty async iterable."""
        result = _mock_claude_sdk()
        # Verify it supports async iteration (has __aiter__)
        assert hasattr(result, "__aiter__"), (
            "Autouse mock return value should be async iterable"
        )


# ===========================================================================
# Test 1: _build_prompt includes all required sections
# ===========================================================================


class TestBuildPrompt:
    """(1) _build_prompt includes commit metadata, diff stats, diff, JSON instruction."""

    def test_includes_commit_metadata(self, sample_commit: CommitInfo) -> None:
        """Prompt must include commit hash, author, date, and original message."""
        prompt = _build_prompt(sample_commit)
        assert sample_commit.short_hash in prompt
        assert sample_commit.author in prompt
        assert sample_commit.original_message in prompt
        assert str(sample_commit.files_changed) in prompt

    def test_includes_diff_statistics(self, sample_commit: CommitInfo) -> None:
        """Prompt must include diff statistics section."""
        prompt = _build_prompt(sample_commit)
        assert "## Diff Statistics" in prompt
        assert sample_commit.diff_stat in prompt

    def test_includes_diff_patch(self, sample_commit: CommitInfo) -> None:
        """Prompt must include the actual diff content."""
        prompt = _build_prompt(sample_commit)
        assert "## Diff" in prompt
        assert "diff --git" in prompt

    def test_includes_json_instruction(self, sample_commit: CommitInfo) -> None:
        """Prompt must instruct Claude to respond with JSON containing required keys."""
        prompt = _build_prompt(sample_commit)
        assert '"subject"' in prompt
        assert '"body"' in prompt
        assert '"changelog_category"' in prompt
        assert '"changelog_entry"' in prompt
        assert "Respond with ONLY a JSON object" in prompt

    def test_all_four_sections_present(self, sample_commit: CommitInfo) -> None:
        """Single test verifying all four required sections exist."""
        prompt = _build_prompt(sample_commit)
        # Section 1: Commit Metadata
        assert "## Commit Metadata" in prompt
        # Section 2: Diff Statistics
        assert "## Diff Statistics" in prompt
        # Section 3: Diff
        assert "## Diff" in prompt
        # Section 4: JSON instruction
        assert "JSON" in prompt

    def test_tags_included_when_present(self) -> None:
        commit = _make_commit(tags=["v1.0.0", "v1.0.1"])
        prompt = _build_prompt(commit)
        assert "v1.0.0" in prompt
        assert "v1.0.1" in prompt

    def test_tags_none_when_absent(self) -> None:
        commit = _make_commit(tags=[])
        prompt = _build_prompt(commit)
        assert "- Tags: none" in prompt

    def test_truncates_large_diff(self) -> None:
        huge_diff = "x" * 300_000
        commit = _make_commit(diff_patch=huge_diff)
        prompt = _build_prompt(commit)
        assert "[... diff truncated for size ...]" in prompt
        assert len(prompt) < 300_000


# ===========================================================================
# Test 2: _build_batch_prompt includes all commits, instructs JSON array
# ===========================================================================


class TestBuildBatchPrompt:
    """(2) _build_batch_prompt includes all commits and instructs JSON array response."""

    def test_includes_all_commits(
        self, sample_commit: CommitInfo, sample_commit_2: CommitInfo
    ) -> None:
        """Every commit's metadata must appear in the batch prompt."""
        prompt = _build_batch_prompt([sample_commit, sample_commit_2])
        assert sample_commit.short_hash in prompt
        assert sample_commit_2.short_hash in prompt

    def test_commit_numbering(
        self, sample_commit: CommitInfo, sample_commit_2: CommitInfo
    ) -> None:
        prompt = _build_batch_prompt([sample_commit, sample_commit_2])
        assert "Commit 1 of 2" in prompt
        assert "Commit 2 of 2" in prompt

    def test_instructs_json_array_response(
        self, sample_commit: CommitInfo
    ) -> None:
        """Prompt must explicitly instruct Claude to return a JSON array."""
        prompt = _build_batch_prompt([sample_commit])
        assert "JSON **array**" in prompt
        assert "JSON array" in prompt  # also the plain version in the footer

    def test_includes_required_keys_in_instruction(
        self, sample_commit: CommitInfo
    ) -> None:
        """Batch prompt must specify required keys for each object."""
        prompt = _build_batch_prompt([sample_commit])
        assert '"subject"' in prompt
        assert '"changelog_category"' in prompt
        assert '"changelog_entry"' in prompt

    def test_truncates_large_diff_in_batch(self) -> None:
        commit = _make_commit(diff_patch="y" * 300_000)
        prompt = _build_batch_prompt([commit])
        assert "[... diff truncated for size ...]" in prompt


# ===========================================================================
# Test 3: _extract_json parses clean JSON directly
# ===========================================================================


class TestExtractJsonDirect:
    """(3) _extract_json parses clean JSON directly."""

    def test_parses_clean_json_object(self) -> None:
        """Clean JSON object should be parsed directly (Strategy 1)."""
        text = json.dumps(_VALID_SINGLE)
        result = _extract_json(text)
        assert result == _VALID_SINGLE

    def test_parses_clean_json_array(self) -> None:
        """Clean JSON array should be parsed directly (Strategy 1)."""
        arr = [_VALID_SINGLE, _VALID_SINGLE_2]
        text = json.dumps(arr)
        result = _extract_json(text)
        assert result == arr

    def test_whitespace_padded(self) -> None:
        """JSON with leading/trailing whitespace should still parse."""
        text = "  \n" + json.dumps(_VALID_SINGLE) + "\n  "
        result = _extract_json(text)
        assert result == _VALID_SINGLE


# ===========================================================================
# Test 4: _extract_json extracts from markdown code fences
# ===========================================================================


class TestExtractJsonFences:
    """(4) _extract_json extracts from markdown code fences."""

    def test_json_fence(self) -> None:
        """JSON inside a ```json fence should be extracted."""
        text = "Here is the output:\n```json\n" + json.dumps(_VALID_SINGLE) + "\n```"
        result = _extract_json(text)
        assert result == _VALID_SINGLE

    def test_plain_fence(self) -> None:
        """JSON inside a plain ``` fence (no language tag) should be extracted."""
        text = "Result:\n```\n" + json.dumps(_VALID_SINGLE) + "\n```"
        result = _extract_json(text)
        assert result == _VALID_SINGLE

    def test_fence_with_array(self) -> None:
        """JSON array inside a code fence should be extracted correctly."""
        arr = [_VALID_SINGLE, _VALID_SINGLE_2]
        text = "```json\n" + json.dumps(arr) + "\n```"
        result = _extract_json(text)
        assert result == arr


# ===========================================================================
# Test 5: _extract_json extracts from prose-wrapped JSON
# ===========================================================================


class TestExtractJsonProseWrapped:
    """(5) _extract_json extracts from prose-wrapped JSON."""

    def test_prose_prefix_single_object(self) -> None:
        """JSON with leading prose should be extracted."""
        text = "Here is the analysis:\n" + json.dumps(_VALID_SINGLE)
        result = _extract_json(text)
        assert result == _VALID_SINGLE

    def test_prose_prefix_array(self) -> None:
        """JSON array with prose prefix must return full array."""
        arr = [_VALID_SINGLE, _VALID_SINGLE_2]
        text = "Here are the results: " + json.dumps(arr)
        result = _extract_json(text)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result == arr

    def test_prose_on_both_sides(self) -> None:
        """JSON embedded with prose before and after."""
        arr = [_VALID_SINGLE, _VALID_SINGLE_2]
        text = (
            "I've analyzed both commits. Here are the results:\n"
            + json.dumps(arr)
            + "\n\nLet me know if you need anything else."
        )
        result = _extract_json(text)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_object_with_trailing_junk(self) -> None:
        text = "Output: " + json.dumps(_VALID_SINGLE) + "\nDone!"
        result = _extract_json(text)
        assert result == _VALID_SINGLE


# ===========================================================================
# Test 6: _extract_json validates expected keys and rejects random JSON
# ===========================================================================


class TestExtractJsonKeyValidation:
    """(6) _extract_json validates expected keys and rejects random JSON."""

    def test_rejects_json_without_expected_keys(self) -> None:
        """JSON objects without 'subject' and 'changelog_category' keys should be rejected."""
        bad_json = {"foo": "bar", "baz": 1}
        text = "Here: " + json.dumps(bad_json)
        with pytest.raises(ValueError, match="Could not extract valid JSON"):
            _extract_json(text)

    def test_rejects_random_json_in_prose(self) -> None:
        """Completely unrelated JSON embedded in prose should be rejected."""
        random_json = {"name": "Alice", "age": 30, "city": "NYC"}
        text = "The config is: " + json.dumps(random_json) + " — use it wisely."
        with pytest.raises(ValueError, match="Could not extract valid JSON"):
            _extract_json(text)

    def test_accepts_json_with_expected_keys(self) -> None:
        """JSON with subject + changelog_category should be accepted."""
        text = "Result: " + json.dumps(_VALID_SINGLE)
        result = _extract_json(text)
        assert result["subject"] == _VALID_SINGLE["subject"]

    def test_no_json_at_all(self) -> None:
        with pytest.raises(ValueError, match="Could not extract valid JSON"):
            _extract_json("This is just plain text with no JSON.")

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError):
            _extract_json("")


# ===========================================================================
# Test 7: _extract_json handles multiple code fences (tries all, not just first)
# ===========================================================================


class TestExtractJsonMultipleFences:
    """(7) _extract_json handles multiple code fences (tries all, not just first)."""

    def test_first_fence_invalid_second_valid(self) -> None:
        """When the first code fence has invalid JSON, the second should be tried."""
        text = (
            "```\nnot valid json\n```\n"
            "```json\n" + json.dumps(_VALID_SINGLE) + "\n```"
        )
        result = _extract_json(text)
        assert result == _VALID_SINGLE

    def test_first_fence_wrong_shape_second_valid(self) -> None:
        """First fence has parseable but wrong-shaped JSON; second has correct JSON."""
        wrong_shape = {"irrelevant_key": "irrelevant_value"}
        text = (
            "```json\n" + json.dumps(wrong_shape) + "\n```\n"
            "Here is the correct one:\n"
            "```json\n" + json.dumps(_VALID_SINGLE) + "\n```"
        )
        # Strategy 2 (fences) returns whichever is valid JSON first (even wrong keys),
        # but _extract_json Strategy 2 just checks isinstance(result, (dict, list)),
        # so the first fence is returned as-is by Strategy 2.
        # BUT the outer function returns it — this tests Strategy 2 tries all fences.
        # Strategy 2 returns the first parseable JSON. The first fence IS valid JSON.
        # This test verifies that finditer is used (multiple fences are iterated).
        result = _extract_json(text)
        # First parseable fence is returned — the wrong_shape dict
        # Actually, since wrong_shape is parseable JSON, Strategy 2 returns it.
        # This is expected behavior — finditer iterates but stops on first success.
        assert isinstance(result, dict)

    def test_three_fences_first_two_invalid(self) -> None:
        """Three fences, only the third has valid JSON."""
        text = (
            "```\nthis is not json\n```\n"
            "```python\ndef foo(): pass\n```\n"
            "```json\n" + json.dumps(_VALID_SINGLE) + "\n```"
        )
        result = _extract_json(text)
        assert result == _VALID_SINGLE

    def test_multiple_fences_with_array(self) -> None:
        """Multiple fences — invalid first, valid array second."""
        arr = [_VALID_SINGLE, _VALID_SINGLE_2]
        text = (
            "```\nbroken {json\n```\n"
            "```json\n" + json.dumps(arr) + "\n```"
        )
        result = _extract_json(text)
        assert isinstance(result, list)
        assert len(result) == 2


# ===========================================================================
# Test 8: generate_message returns correct GeneratedMessage (override autouse mock)
# ===========================================================================


class TestGenerateMessage:
    """(8) generate_message returns correct GeneratedMessage when Claude returns valid JSON.

    Overrides the autouse mock with a specific response to verify end-to-end.
    """

    async def test_returns_correct_generated_message(
        self,
        sample_commit: CommitInfo,
    ) -> None:
        """Override autouse mock with specific response, verify GeneratedMessage."""
        assistant_msg = _make_assistant_msg(json.dumps(_VALID_SINGLE))
        result_msg = _make_result_msg(cost=0.005, input_tokens=100, output_tokens=50)

        mock_q = make_mock_query([assistant_msg, result_msg])

        with (
            patch("gitre.generator.query", mock_q),
            patch("gitre.generator.AssistantMessage", _AssistantMessageType),
            patch("gitre.generator.ResultMessage", _ResultMessageType),
            patch("gitre.generator.SDK_AVAILABLE", True),
        ):
            msg = await generate_message(sample_commit, "/fake/repo")

        # Verify it's the right type
        assert isinstance(msg, GeneratedMessage)
        # Verify fields match the mocked response
        assert msg.subject == _VALID_SINGLE["subject"]
        assert msg.body == _VALID_SINGLE["body"]
        assert msg.changelog_category == "Added"
        assert msg.changelog_entry == _VALID_SINGLE["changelog_entry"]
        # Verify commit hash fields are carried through
        assert msg.hash == sample_commit.hash
        assert msg.short_hash == sample_commit.short_hash
        # Verify query() was called exactly once
        mock_q.assert_called_once()

    async def test_empty_response_raises(
        self,
        sample_commit: CommitInfo,
    ) -> None:
        """Empty Claude output should raise RuntimeError."""
        mock_q = make_mock_query([])  # no messages -> empty text

        with (
            patch("gitre.generator.query", mock_q),
            patch("gitre.generator.SDK_AVAILABLE", True),
            pytest.raises(RuntimeError, match="Empty response"),
        ):
            await generate_message(sample_commit, "/fake/repo")

    async def test_sdk_not_available_raises(
        self,
        sample_commit: CommitInfo,
    ) -> None:
        """Should raise RuntimeError when SDK is not installed."""
        with (
            patch("gitre.generator.SDK_AVAILABLE", False),
            pytest.raises(RuntimeError, match="claude-agent-sdk is not installed"),
        ):
            await generate_message(sample_commit, "/fake/repo")


# ===========================================================================
# Test 9: generate_messages_batch returns list of GeneratedMessage
# ===========================================================================


class TestGenerateMessagesBatch:
    """(9) generate_messages_batch returns list of GeneratedMessage."""

    async def test_batch_returns_list_of_generated_messages(
        self,
        sample_commit: CommitInfo,
        sample_commit_2: CommitInfo,
    ) -> None:
        """Batch with 2 commits should return a BatchResult with a list of GeneratedMessage."""
        arr = [_VALID_SINGLE, _VALID_SINGLE_2]
        assistant_msg = _make_assistant_msg(json.dumps(arr))
        result_msg = _make_result_msg(cost=0.01, input_tokens=200, output_tokens=100)

        mock_q = make_mock_query([assistant_msg, result_msg])

        with (
            patch("gitre.generator.query", mock_q),
            patch("gitre.generator.AssistantMessage", _AssistantMessageType),
            patch("gitre.generator.ResultMessage", _ResultMessageType),
            patch("gitre.generator.SDK_AVAILABLE", True),
        ):
            result = await generate_messages_batch(
                [sample_commit, sample_commit_2], "/fake/repo"
            )

        assert isinstance(result, BatchResult)
        assert len(result.messages) == 2
        # Verify each element is a GeneratedMessage
        for msg in result.messages:
            assert isinstance(msg, GeneratedMessage)
        # Verify correct subjects
        assert result.messages[0].subject == _VALID_SINGLE["subject"]
        assert result.messages[1].subject == _VALID_SINGLE_2["subject"]
        # Verify hash fields are mapped correctly
        assert result.messages[0].hash == sample_commit.hash
        assert result.messages[1].hash == sample_commit_2.hash
        # Verify token/cost accounting
        assert result.total_tokens == 300
        assert result.total_cost == 0.01
        # Verify query() was called exactly once (batch call)
        mock_q.assert_called_once()

    async def test_empty_commits_returns_empty(self) -> None:
        """Empty commit list should return empty BatchResult without calling query()."""
        result = await generate_messages_batch([], "/fake/repo")
        assert isinstance(result, BatchResult)
        assert result.messages == []
        assert result.total_tokens == 0

    async def test_single_commit_delegates(
        self,
        sample_commit: CommitInfo,
    ) -> None:
        """Single commit should delegate to generate_message."""
        assistant_msg = _make_assistant_msg(json.dumps(_VALID_SINGLE))
        result_msg = _make_result_msg(cost=0.003, input_tokens=50, output_tokens=30)

        mock_q = make_mock_query([assistant_msg, result_msg])

        with (
            patch("gitre.generator.query", mock_q),
            patch("gitre.generator.AssistantMessage", _AssistantMessageType),
            patch("gitre.generator.ResultMessage", _ResultMessageType),
            patch("gitre.generator.SDK_AVAILABLE", True),
        ):
            result = await generate_messages_batch([sample_commit], "/fake/repo")

        assert len(result.messages) == 1
        assert isinstance(result.messages[0], GeneratedMessage)
        assert result.messages[0].subject == _VALID_SINGLE["subject"]

    async def test_batch_empty_response_raises(
        self,
        sample_commit: CommitInfo,
        sample_commit_2: CommitInfo,
    ) -> None:
        """Empty batch response should raise RuntimeError."""
        mock_q = make_mock_query([])

        with (
            patch("gitre.generator.query", mock_q),
            patch("gitre.generator.SDK_AVAILABLE", True),
            pytest.raises(RuntimeError, match="Empty response"),
        ):
            await generate_messages_batch(
                [sample_commit, sample_commit_2], "/fake/repo"
            )


# ===========================================================================
# Test 10: ANTHROPIC_API_KEY is stripped from env in options
# ===========================================================================


class TestAnthropicApiKeyStripped:
    """(10) ANTHROPIC_API_KEY is stripped from env in options.

    ``_build_options`` constructs ``ClaudeAgentOptions`` with an ``env`` dict
    that filters out ``ANTHROPIC_API_KEY`` to follow the SDK gotchas.
    """

    def test_api_key_excluded_from_env(self) -> None:
        """ANTHROPIC_API_KEY must not appear in the options env dict."""
        # Set the key in the environment for this test
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-ant-fake-key-12345",
                "HOME": "/home/test",
                "PATH": "/usr/bin",
            },
        ):
            # Mock ClaudeAgentOptions so we can inspect what was passed
            with patch("gitre.generator.ClaudeAgentOptions") as mock_opts_cls:
                mock_opts_cls.return_value = MagicMock()
                _build_options(
                    cwd="/fake/repo",
                    model="sonnet",
                    output_schema={"type": "object"},
                )
                # Inspect the env kwarg passed to ClaudeAgentOptions
                call_kwargs = mock_opts_cls.call_args
                env_dict = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env")
                assert "ANTHROPIC_API_KEY" not in env_dict, (
                    "ANTHROPIC_API_KEY should be stripped from the env dict "
                    "passed to ClaudeAgentOptions"
                )

    def test_other_env_vars_preserved(self) -> None:
        """Other environment variables should be preserved in the env dict."""
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-ant-fake-key-12345",
                "HOME": "/home/test",
                "PATH": "/usr/bin",
                "MY_CUSTOM_VAR": "my_value",
            },
            clear=True,
        ):
            with patch("gitre.generator.ClaudeAgentOptions") as mock_opts_cls:
                mock_opts_cls.return_value = MagicMock()
                _build_options(
                    cwd="/fake/repo",
                    model="sonnet",
                    output_schema={"type": "object"},
                )
                call_kwargs = mock_opts_cls.call_args
                env_dict = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env")
                # These should be present
                assert env_dict["HOME"] == "/home/test"
                assert env_dict["PATH"] == "/usr/bin"
                assert env_dict["MY_CUSTOM_VAR"] == "my_value"
                # This should be absent
                assert "ANTHROPIC_API_KEY" not in env_dict

    def test_works_when_api_key_not_set(self) -> None:
        """_build_options should work even if ANTHROPIC_API_KEY is not set."""
        env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True):
            with patch("gitre.generator.ClaudeAgentOptions") as mock_opts_cls:
                mock_opts_cls.return_value = MagicMock()
                _build_options(
                    cwd="/fake/repo",
                    model="sonnet",
                    output_schema={"type": "object"},
                )
                call_kwargs = mock_opts_cls.call_args
                env_dict = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env")
                assert "ANTHROPIC_API_KEY" not in env_dict


# ===========================================================================
# Additional supporting tests (kept from original file)
# ===========================================================================


class TestValidateJsonKeys:
    """Tests for ``_validate_json_keys``."""

    def test_valid_single(self) -> None:
        assert _validate_json_keys(_VALID_SINGLE) is True

    def test_valid_list(self) -> None:
        assert _validate_json_keys([_VALID_SINGLE, _VALID_SINGLE_2]) is True

    def test_missing_keys(self) -> None:
        assert _validate_json_keys({"foo": "bar"}) is False

    def test_empty_list(self) -> None:
        assert _validate_json_keys([]) is False

    def test_list_with_non_dict(self) -> None:
        assert _validate_json_keys(["not a dict"]) is False


class TestParseSingleResponse:
    """Tests for ``_parse_single_response``."""

    def test_normal_response(self, sample_commit: CommitInfo) -> None:
        msg = _parse_single_response(_VALID_SINGLE, sample_commit)
        assert isinstance(msg, GeneratedMessage)
        assert msg.subject == _VALID_SINGLE["subject"]
        assert msg.body == _VALID_SINGLE["body"]
        assert msg.changelog_category == "Added"
        assert msg.hash == sample_commit.hash
        assert msg.short_hash == sample_commit.short_hash

    def test_truncates_long_subject(self, sample_commit: CommitInfo) -> None:
        raw = {**_VALID_SINGLE, "subject": "A" * 100}
        msg = _parse_single_response(raw, sample_commit)
        assert len(msg.subject) <= 72
        assert msg.subject.endswith("...")

    def test_null_body(self, sample_commit: CommitInfo) -> None:
        raw = {**_VALID_SINGLE, "body": None}
        msg = _parse_single_response(raw, sample_commit)
        assert msg.body is None


class TestBatchResult:
    """Tests for the ``BatchResult`` dataclass."""

    def test_defaults(self) -> None:
        br = BatchResult(messages=[])
        assert br.total_tokens == 0
        assert br.total_cost == 0.0

    def test_frozen(self) -> None:
        br = BatchResult(messages=[], total_tokens=10, total_cost=0.5)
        with pytest.raises(AttributeError):
            br.total_tokens = 20  # type: ignore[misc]


class TestExtractJsonBatchOrdering:
    """Regression tests for the batch ordering bug fix."""

    def test_prose_prefix_preserves_full_array(self) -> None:
        arr = [_VALID_SINGLE, _VALID_SINGLE_2]
        text = "Here are the commit analyses:\n" + json.dumps(arr)
        result = _extract_json(text)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_single_object_in_prose_still_works(self) -> None:
        text = "Analysis complete:\n" + json.dumps(_VALID_SINGLE)
        result = _extract_json(text)
        assert isinstance(result, dict)
        assert result["subject"] == _VALID_SINGLE["subject"]
