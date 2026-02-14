"""Manages the .gitre/ cache directory inside a target repository.

Provides functions to save, load, validate, and clear cached analysis
results stored as JSON in the .gitre/ directory of the analysed repo.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from gitre.models import AnalysisResult

_CACHE_DIR = ".gitre"
_ANALYSIS_FILE = "analysis.json"


def _gitre_dir(repo_path: str) -> Path:
    """Return the .gitre/ directory path for a given repository."""
    return Path(repo_path) / _CACHE_DIR


def _analysis_path(repo_path: str) -> Path:
    """Return the path to the analysis.json file."""
    return _gitre_dir(repo_path) / _ANALYSIS_FILE


def save_analysis(repo_path: str, result: AnalysisResult) -> None:
    """Write an AnalysisResult to .gitre/analysis.json.

    Creates the .gitre/ directory if it doesn't exist, writes a
    .gitre/.gitignore to exclude analysis.json from version control,
    and auto-adds '.gitre/' to the target repo's root .gitignore if
    that file exists.

    Args:
        repo_path: Path to the target git repository.
        result: The analysis result to persist.
    """
    gitre_dir = _gitre_dir(repo_path)
    gitre_dir.mkdir(parents=True, exist_ok=True)

    # Write the analysis result as JSON (mode='json' handles datetime serialization)
    analysis_file = gitre_dir / _ANALYSIS_FILE
    data = result.model_dump(mode="json")
    analysis_file.write_text(json.dumps(data, indent=2), encoding="utf-8")



def load_analysis(repo_path: str) -> AnalysisResult:
    """Read and parse .gitre/analysis.json into an AnalysisResult.

    Args:
        repo_path: Path to the target git repository.

    Returns:
        The deserialised AnalysisResult.

    Raises:
        FileNotFoundError: If analysis.json does not exist.
        pydantic.ValidationError: If the JSON does not match the schema.
    """
    analysis_file = _analysis_path(repo_path)
    raw = analysis_file.read_text(encoding="utf-8")
    data = json.loads(raw)
    return AnalysisResult.model_validate(data)


def validate_cache(repo_path: str, result: AnalysisResult) -> tuple[bool, str]:
    """Check whether the cached result still matches the current HEAD.

    Compares the result's head_hash against the repository's current
    HEAD commit hash.

    Args:
        repo_path: Path to the target git repository.
        result: The cached analysis result to validate.

    Returns:
        A tuple of (is_valid, warning_message).  When valid the
        warning_message is an empty string.
    """
    try:
        current_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_path,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return False, f"Unable to determine current HEAD: {exc}"

    if current_head == result.head_hash:
        return True, ""

    return (
        False,
        f"Cache is stale: cached HEAD {result.head_hash[:8]} "
        f"does not match current HEAD {current_head[:8]}.",
    )


def clear_cache(repo_path: str) -> None:
    """Remove .gitre/analysis.json if it exists.

    Args:
        repo_path: Path to the target git repository.
    """
    analysis_file = _analysis_path(repo_path)
    if analysis_file.exists():
        analysis_file.unlink()


