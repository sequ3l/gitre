"""Pydantic v2 models for gitre commit analysis and message generation."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Valid changelog categories per Keep a Changelog convention
CHANGELOG_CATEGORIES = frozenset(
    {"Added", "Changed", "Fixed", "Removed", "Deprecated", "Security"}
)


class CommitInfo(BaseModel):
    """Raw commit information extracted from a git repository."""

    model_config = ConfigDict(frozen=True)

    hash: str
    short_hash: str
    author: str
    date: datetime
    original_message: str
    diff_stat: str
    diff_patch: str
    files_changed: int
    insertions: int
    deletions: int
    tags: list[str] = Field(default_factory=list)


class GeneratedMessage(BaseModel):
    """LLM-generated commit message and changelog entry for a single commit."""

    model_config = ConfigDict(frozen=True)

    hash: str
    short_hash: str
    subject: str = Field(
        ...,
        max_length=72,
        description="Commit subject line, must be 72 characters or fewer.",
    )
    body: str | None = None
    changelog_category: str = Field(
        ...,
        description="Changelog category: one of Added, Changed, Fixed, Removed, Deprecated, or Security.",
    )
    changelog_entry: str

    @field_validator("subject")
    @classmethod
    def subject_must_be_short(cls, v: str) -> str:
        """Ensure subject line does not exceed 72 characters."""
        if len(v) > 72:
            raise ValueError(
                f"Subject must be 72 characters or fewer, got {len(v)}"
            )
        return v

    @field_validator("changelog_category")
    @classmethod
    def changelog_category_must_be_valid(cls, v: str) -> str:
        """Ensure changelog_category is one of the accepted values."""
        if v not in CHANGELOG_CATEGORIES:
            raise ValueError(
                f"changelog_category must be one of {sorted(CHANGELOG_CATEGORIES)}, got {v!r}"
            )
        return v


class AnalysisResult(BaseModel):
    """Complete analysis result for a range of commits in a repository."""

    model_config = ConfigDict(frozen=True)

    repo_path: str
    head_hash: str
    from_ref: str | None = None
    to_ref: str | None = None
    commits_analyzed: int
    messages: list[GeneratedMessage] = Field(default_factory=list)
    tags: dict[str, str] = Field(default_factory=dict)
    total_tokens: int = 0
    total_cost: float = 0.0
    analyzed_at: datetime = Field(default_factory=datetime.now)
