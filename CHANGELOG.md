# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-02-14

### Added

- `gitre analyze` command — walks git history, extracts diffs, and generates meaningful commit messages and changelog entries via Claude (claude-agent-sdk)
- `gitre commit` command — applies cached analysis results to rewrite git history without re-calling Claude
- Keep a Changelog formatted output with version tag grouping and comparison links
- Two-step workflow: analyze first, review proposals, then commit — or use `--live` for one-shot operation
- Selective commit application with `--only` and `--skip` filters
- Commit range support via `--from` and `--to` refs
- Batch mode (`--batch-size`) for analyzing multiple commits per Claude call
- Model selection (`--model`) supporting sonnet, opus, and haiku
- `.gitre/` cache directory for staging analysis results between analyze and commit
- Cache validation with HEAD hash staleness detection
- Automatic backup branch creation (`gitre-backup-{timestamp}`) before any history rewrite
- Git history rewriting via git-filter-repo integration with content-matching callbacks
- Large diff truncation (>50KB) with file-level summaries
- Robust multi-strategy JSON extraction from Claude responses
- Pydantic v2 models for CommitInfo, GeneratedMessage, and AnalysisResult
- Rich console output for progress and proposal display
- Comprehensive test suite with autouse Claude SDK mock fixture to prevent real API calls
- README with prerequisites, Claude Code / Agent SDK integration details, full command reference, and usage examples
- CLAUDE.md project instructions with workflow reminders and Claude Agent SDK configuration notes
- COMMANDS.md with full CLI reference, options, and workflow examples

### Changed

- Default `--model` changed from `sonnet` to `opus` for higher quality analysis
- Stop auto-gitignoring `.gitre/` directory — analysis cache is now tracked by git so it survives history rewrites and repo restores
- Progress output (spinners, status messages) now always shown during analysis, not only with `--verbose`
- `--verbose` / `-v` now adds per-commit hash detail instead of being required for any feedback
- `git-filter-repo` moved from optional to required dependency — installed automatically with gitre

### Fixed

- Write filter-repo callback to temp file instead of passing inline — fixes Windows command-line length limit (WinError 206) on repos with many commits
- Switch from `--message-callback` to `--commit-callback` with hash-based matching — fixes all commits getting the same message when many share identical original messages (e.g. "etc")
- Force UTF-8 encoding on Rich console output — fixes `UnicodeEncodeError` crash on Windows (cp1252) when commit messages contain Unicode characters
- Stop deleting analysis.json after history rewrite — cache is preserved for safety and re-runs
