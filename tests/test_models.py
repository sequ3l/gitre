"""Tests for Pydantic model validation in gitre.models.

Covers:
1. CommitInfo accepts valid data and serializes/deserializes correctly.
2. GeneratedMessage validates subject length (must be <= 72 chars).
3. GeneratedMessage validates changelog_category is one of the allowed values.
4. AnalysisResult round-trips through model_dump / model_validate including datetime.
5. Default values and optional fields (body=None, tags=[], from_ref=None, to_ref=None).
6. Invalid data raises ValidationError where appropriate.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from gitre.models import (
    CHANGELOG_CATEGORIES,
    AnalysisResult,
    CommitInfo,
    GeneratedMessage,
)

# ── helpers ──────────────────────────────────────────────────────────────

_VALID_COMMIT_DATA: dict = {
    "hash": "a" * 40,
    "short_hash": "a" * 7,
    "author": "Alice <alice@example.com>",
    "date": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
    "original_message": "initial commit",
    "diff_stat": " file.py | 1 +\n 1 file changed, 1 insertion(+)",
    "diff_patch": "diff --git a/file.py b/file.py\n+hello\n",
    "files_changed": 1,
    "insertions": 1,
    "deletions": 0,
}

_VALID_MSG_DATA: dict = {
    "hash": "b" * 40,
    "short_hash": "b" * 7,
    "subject": "Fix null-pointer in user loader",
    "body": "Detailed explanation of the fix.",
    "changelog_category": "Fixed",
    "changelog_entry": "Resolved null-pointer when loading users with no email.",
}

_VALID_ANALYSIS_DATA: dict = {
    "repo_path": "/tmp/repo",
    "head_hash": "c" * 40,
    "commits_analyzed": 0,
}


# =========================================================================
# 1. CommitInfo — valid data & serialization round-trip
# =========================================================================


class TestCommitInfoValid:
    """CommitInfo accepts valid data and serializes/deserializes correctly."""

    def test_construction_with_all_fields(self) -> None:
        data = {**_VALID_COMMIT_DATA, "tags": ["v1.0.0", "release"]}
        ci = CommitInfo(**data)

        assert ci.hash == data["hash"]
        assert ci.short_hash == data["short_hash"]
        assert ci.author == data["author"]
        assert ci.date == data["date"]
        assert ci.original_message == data["original_message"]
        assert ci.files_changed == 1
        assert ci.insertions == 1
        assert ci.deletions == 0
        assert ci.tags == ["v1.0.0", "release"]

    def test_model_dump_returns_dict(self) -> None:
        ci = CommitInfo(**_VALID_COMMIT_DATA)
        dumped = ci.model_dump()
        assert isinstance(dumped, dict)
        assert dumped["hash"] == _VALID_COMMIT_DATA["hash"]

    def test_round_trip_model_dump_validate(self) -> None:
        ci = CommitInfo(**_VALID_COMMIT_DATA, tags=["v2.0.0"])
        dumped = ci.model_dump()
        restored = CommitInfo.model_validate(dumped)
        assert restored == ci

    def test_json_round_trip(self) -> None:
        ci = CommitInfo(**_VALID_COMMIT_DATA)
        json_str = ci.model_dump_json()
        restored = CommitInfo.model_validate_json(json_str)
        assert restored == ci

    def test_fixture_sample_commit(self, sample_commit: CommitInfo) -> None:
        """Fixtures from conftest.py should produce valid models."""
        assert sample_commit.hash
        assert sample_commit.short_hash
        assert isinstance(sample_commit.date, datetime)

    def test_fixture_sample_commit_info(self, sample_commit_info: CommitInfo) -> None:
        """sample_commit_info fixture includes tags."""
        assert len(sample_commit_info.tags) == 2
        assert "v0.3.0" in sample_commit_info.tags


# =========================================================================
# 2. GeneratedMessage — subject length validation
# =========================================================================


class TestSubjectLengthValidation:
    """GeneratedMessage validates that subject is <= 72 characters."""

    def test_subject_exactly_72_chars(self) -> None:
        subject = "x" * 72
        msg = GeneratedMessage(**{**_VALID_MSG_DATA, "subject": subject})
        assert len(msg.subject) == 72

    def test_subject_under_72_chars(self) -> None:
        subject = "Short subject"
        msg = GeneratedMessage(**{**_VALID_MSG_DATA, "subject": subject})
        assert msg.subject == subject

    def test_subject_73_chars_raises(self) -> None:
        subject = "x" * 73
        with pytest.raises(ValidationError, match="72"):
            GeneratedMessage(**{**_VALID_MSG_DATA, "subject": subject})

    def test_subject_200_chars_raises(self) -> None:
        subject = "y" * 200
        with pytest.raises(ValidationError, match="72"):
            GeneratedMessage(**{**_VALID_MSG_DATA, "subject": subject})

    def test_empty_subject_accepted(self) -> None:
        """An empty subject is technically valid (no min_length constraint)."""
        msg = GeneratedMessage(**{**_VALID_MSG_DATA, "subject": ""})
        assert msg.subject == ""

    def test_subject_single_char(self) -> None:
        msg = GeneratedMessage(**{**_VALID_MSG_DATA, "subject": "A"})
        assert msg.subject == "A"


# =========================================================================
# 3. GeneratedMessage — changelog_category validation
# =========================================================================


class TestChangelogCategoryValidation:
    """GeneratedMessage validates changelog_category is one of the allowed values."""

    @pytest.mark.parametrize("category", sorted(CHANGELOG_CATEGORIES))
    def test_valid_categories_accepted(self, category: str) -> None:
        msg = GeneratedMessage(**{**_VALID_MSG_DATA, "changelog_category": category})
        assert msg.changelog_category == category

    def test_invalid_category_raises(self) -> None:
        with pytest.raises(ValidationError, match="changelog_category"):
            GeneratedMessage(**{**_VALID_MSG_DATA, "changelog_category": "Improved"})

    def test_lowercase_category_raises(self) -> None:
        """Categories are case-sensitive; 'added' is not valid."""
        with pytest.raises(ValidationError, match="changelog_category"):
            GeneratedMessage(**{**_VALID_MSG_DATA, "changelog_category": "added"})

    def test_empty_category_raises(self) -> None:
        with pytest.raises(ValidationError, match="changelog_category"):
            GeneratedMessage(**{**_VALID_MSG_DATA, "changelog_category": ""})

    def test_all_six_categories_exist(self) -> None:
        expected = {"Added", "Changed", "Fixed", "Removed", "Deprecated", "Security"}
        assert CHANGELOG_CATEGORIES == expected


# =========================================================================
# 4. AnalysisResult — round-trip including datetime fields
# =========================================================================


class TestAnalysisResultRoundTrip:
    """AnalysisResult round-trips through model_dump / model_validate."""

    def test_round_trip_with_datetime(self) -> None:
        analyzed_at = datetime(2026, 6, 15, 8, 30, 0, tzinfo=UTC)
        ar = AnalysisResult(
            **_VALID_ANALYSIS_DATA,
            analyzed_at=analyzed_at,
        )
        dumped = ar.model_dump()
        restored = AnalysisResult.model_validate(dumped)
        assert restored == ar
        assert restored.analyzed_at == analyzed_at

    def test_round_trip_preserves_messages(self) -> None:
        msg = GeneratedMessage(**_VALID_MSG_DATA)
        ar = AnalysisResult(
            **{**_VALID_ANALYSIS_DATA, "commits_analyzed": 1},
            messages=[msg],
        )
        dumped = ar.model_dump()
        restored = AnalysisResult.model_validate(dumped)
        assert len(restored.messages) == 1
        assert restored.messages[0].subject == msg.subject

    def test_json_round_trip(self) -> None:
        msg = GeneratedMessage(**_VALID_MSG_DATA)
        ar = AnalysisResult(
            **{**_VALID_ANALYSIS_DATA, "commits_analyzed": 1},
            messages=[msg],
            tags={"abc": "v1.0.0"},
            total_tokens=1000,
            total_cost=0.03,
            analyzed_at=datetime(2026, 2, 14, 12, 0, 0, tzinfo=UTC),
        )
        json_str = ar.model_dump_json()
        restored = AnalysisResult.model_validate_json(json_str)
        assert restored == ar

    def test_fixture_round_trip(self, sample_analysis_result: AnalysisResult) -> None:
        """The conftest fixture round-trips cleanly."""
        dumped = sample_analysis_result.model_dump()
        restored = AnalysisResult.model_validate(dumped)
        assert restored == sample_analysis_result
        assert isinstance(restored.analyzed_at, datetime)
        assert len(restored.messages) == 3


# =========================================================================
# 5. Default values and optional fields
# =========================================================================


class TestDefaultsAndOptionals:
    """Test default values and optional fields across all models."""

    # CommitInfo.tags defaults to []
    def test_commit_info_tags_default_empty_list(self) -> None:
        ci = CommitInfo(**_VALID_COMMIT_DATA)
        assert ci.tags == []

    def test_commit_info_tags_default_is_independent(self) -> None:
        """Each instance gets its own list (no mutable default sharing)."""
        ci1 = CommitInfo(**_VALID_COMMIT_DATA)
        ci2 = CommitInfo(**_VALID_COMMIT_DATA)
        assert ci1.tags is not ci2.tags

    # GeneratedMessage.body defaults to None
    def test_generated_message_body_default_none(self) -> None:
        data = {k: v for k, v in _VALID_MSG_DATA.items() if k != "body"}
        msg = GeneratedMessage(**data)
        assert msg.body is None

    def test_generated_message_body_explicit_none(self) -> None:
        msg = GeneratedMessage(**{**_VALID_MSG_DATA, "body": None})
        assert msg.body is None

    def test_generated_message_body_explicit_string(self) -> None:
        msg = GeneratedMessage(**{**_VALID_MSG_DATA, "body": "Some body text."})
        assert msg.body == "Some body text."

    # AnalysisResult optional / default fields
    def test_analysis_result_from_ref_default_none(self) -> None:
        ar = AnalysisResult(**_VALID_ANALYSIS_DATA)
        assert ar.from_ref is None

    def test_analysis_result_to_ref_default_none(self) -> None:
        ar = AnalysisResult(**_VALID_ANALYSIS_DATA)
        assert ar.to_ref is None

    def test_analysis_result_messages_default_empty(self) -> None:
        ar = AnalysisResult(**_VALID_ANALYSIS_DATA)
        assert ar.messages == []

    def test_analysis_result_tags_default_empty_dict(self) -> None:
        ar = AnalysisResult(**_VALID_ANALYSIS_DATA)
        assert ar.tags == {}

    def test_analysis_result_total_tokens_default_zero(self) -> None:
        ar = AnalysisResult(**_VALID_ANALYSIS_DATA)
        assert ar.total_tokens == 0

    def test_analysis_result_total_cost_default_zero(self) -> None:
        ar = AnalysisResult(**_VALID_ANALYSIS_DATA)
        assert ar.total_cost == 0.0

    def test_analysis_result_analyzed_at_auto_populated(self) -> None:
        before = datetime.now()
        ar = AnalysisResult(**_VALID_ANALYSIS_DATA)
        after = datetime.now()
        assert before <= ar.analyzed_at <= after

    def test_analysis_result_from_ref_explicit(self) -> None:
        ar = AnalysisResult(**_VALID_ANALYSIS_DATA, from_ref="v0.1.0")
        assert ar.from_ref == "v0.1.0"

    def test_analysis_result_to_ref_explicit(self) -> None:
        ar = AnalysisResult(**_VALID_ANALYSIS_DATA, to_ref="HEAD")
        assert ar.to_ref == "HEAD"


# =========================================================================
# 6. Invalid data raises ValidationError
# =========================================================================


class TestInvalidDataRaises:
    """Invalid data raises ValidationError where appropriate."""

    # --- CommitInfo ---

    def test_commit_info_missing_required_field(self) -> None:
        data = {k: v for k, v in _VALID_COMMIT_DATA.items() if k != "hash"}
        with pytest.raises(ValidationError, match="hash"):
            CommitInfo(**data)

    def test_commit_info_wrong_type_files_changed(self) -> None:
        with pytest.raises(ValidationError):
            CommitInfo(**{**_VALID_COMMIT_DATA, "files_changed": "not-an-int"})

    def test_commit_info_wrong_type_date(self) -> None:
        with pytest.raises(ValidationError):
            CommitInfo(**{**_VALID_COMMIT_DATA, "date": "not-a-date"})

    def test_commit_info_frozen_immutable(self) -> None:
        ci = CommitInfo(**_VALID_COMMIT_DATA)
        with pytest.raises(ValidationError):
            ci.hash = "new_hash"  # type: ignore[misc]

    # --- GeneratedMessage ---

    def test_generated_message_missing_subject(self) -> None:
        data = {k: v for k, v in _VALID_MSG_DATA.items() if k != "subject"}
        with pytest.raises(ValidationError, match="subject"):
            GeneratedMessage(**data)

    def test_generated_message_missing_changelog_entry(self) -> None:
        data = {k: v for k, v in _VALID_MSG_DATA.items() if k != "changelog_entry"}
        with pytest.raises(ValidationError, match="changelog_entry"):
            GeneratedMessage(**data)

    def test_generated_message_frozen_immutable(self) -> None:
        msg = GeneratedMessage(**_VALID_MSG_DATA)
        with pytest.raises(ValidationError):
            msg.subject = "New subject"  # type: ignore[misc]

    def test_generated_message_subject_wrong_type(self) -> None:
        with pytest.raises(ValidationError):
            GeneratedMessage(**{**_VALID_MSG_DATA, "subject": 12345})

    # --- AnalysisResult ---

    def test_analysis_result_missing_repo_path(self) -> None:
        data = {k: v for k, v in _VALID_ANALYSIS_DATA.items() if k != "repo_path"}
        with pytest.raises(ValidationError, match="repo_path"):
            AnalysisResult(**data)

    def test_analysis_result_missing_head_hash(self) -> None:
        data = {k: v for k, v in _VALID_ANALYSIS_DATA.items() if k != "head_hash"}
        with pytest.raises(ValidationError, match="head_hash"):
            AnalysisResult(**data)

    def test_analysis_result_commits_analyzed_wrong_type(self) -> None:
        with pytest.raises(ValidationError):
            AnalysisResult(**{**_VALID_ANALYSIS_DATA, "commits_analyzed": "many"})

    def test_analysis_result_frozen_immutable(self) -> None:
        ar = AnalysisResult(**_VALID_ANALYSIS_DATA)
        with pytest.raises(ValidationError):
            ar.repo_path = "/other/path"  # type: ignore[misc]

    def test_analysis_result_messages_invalid_element(self) -> None:
        """messages list must contain valid GeneratedMessage objects."""
        with pytest.raises(ValidationError):
            AnalysisResult(**{**_VALID_ANALYSIS_DATA, "messages": [{"bad": "data"}]})

    def test_analysis_result_total_tokens_wrong_type(self) -> None:
        with pytest.raises(ValidationError):
            AnalysisResult(**{**_VALID_ANALYSIS_DATA, "total_tokens": "lots"})

    def test_analysis_result_total_cost_wrong_type(self) -> None:
        with pytest.raises(ValidationError):
            AnalysisResult(**{**_VALID_ANALYSIS_DATA, "total_cost": "cheap"})
