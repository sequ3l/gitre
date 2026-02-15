# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Fixed
- Fix `restore_remotes` crashing with exit status 3 when git-filter-repo doesn't strip existing remotes — now uses `git remote set-url` as a fallback instead of unconditionally calling `git remote add`.
- Stop tracking `.gitre/` analysis cache in git (now gitignored) to prevent git-filter-repo from rewriting cache files during history rewrites.
- Stop force-adding `.gitre/` in `commit_artifacts` — only the changelog file is committed after a rewrite.

### Added
- Add GitHub Actions release workflow and PyInstaller spec for building standalone executables on Windows (amd64), Linux (amd64), and macOS (arm64). Tag a `v*` release to trigger automated builds and GitHub Release creation.

### Changed
- Refactor git-filter-repo integration from subprocess calls to Python library API (`RepoFilter`), bundling it into the executable so users no longer need to install git-filter-repo separately.
- Add `gitre label` command: generate AI-powered commit messages for staged changes and commit in one step, with `--all` to stage everything, `--push` for regular push, and `--model` to pick the Claude model.
- Initial release of gitre CLI with `analyze` and `commit` commands for AI-powered git commit message reconstruction, Keep a Changelog output, cache management, git-filter-repo history rewriting, and a full test suite.
- README with prerequisites, Claude Code / Agent SDK integration details, full command reference with option tables, usage examples, safety notes, and project structure overview.
- CLAUDE.md project instructions with workflow reminders and Claude Agent SDK configuration notes.
- COMMANDS.md with full CLI reference covering analyze and commit subcommands, their options, and typical workflow examples.
- Add automatic remote save/restore around git-filter-repo rewrites, post-rewrite artifact commit for .gitre/ cache and changelog, and a --push flag on `analyze --live` and `commit` to force-push rewritten history to the remote.
- Add input validation to the `analyze` command that rejects `--push` when `--live` is not also specified, exiting early with a descriptive error message.
- Add MIT LICENSE file, GitHub Actions CI workflow (tests, ruff, mypy on Python 3.11–3.13/Ubuntu/Windows), cleaner Windows UTF-8 console handling, and complete type hints for CLI internals.

### Changed
- Progress output (spinners, status messages) is now always shown during analysis, not only with `--verbose`. The `--verbose` flag now adds per-commit hash detail for debugging, and batch generation now includes progress spinners that were previously missing.
- git-filter-repo moved from optional to required dependency — it is now installed automatically with gitre. Removed the unused [rewrite] optional extra along with stale tree-sitter entries.
- Stop auto-gitignoring `.gitre/` directory — analysis cache is now tracked by git so it survives history rewrites and repo restores.
- Default `--model` changed from `sonnet` to `opus` for higher quality analysis.
- Rewrite README with full two-phase pipeline documentation, compatibility matrix for major Git hosting platforms, corrected CLI defaults (Python 3.11+, --model opus, --push flag), and expanded safety section.
- Rewrite CHANGELOG entries for conciseness and accuracy: consolidate verbose bullet points, improve formatting consistency, add missing details about --push validation and artifact commit fix, and reorganize entries across Added/Changed/Fixed sections.
- Rewrite CHANGELOG.md entries for clarity and conciseness, consolidating verbose descriptions, improving formatting consistency, and adding a missing fix for upstream tracking after git-filter-repo rewrites.

### Fixed
- Write filter-repo callback to a temp file instead of passing it inline, fixing Windows command-line length limit (WinError 206) on repos with many commits.
- Switch from `--message-callback` to `--commit-callback` with hash-based matching — fixes all commits getting the same rewritten message when many share identical original messages (e.g. "etc").
- Fix UnicodeEncodeError crash on Windows (cp1252) by forcing UTF-8 encoding on Rich console output, and preserve the analysis cache after history rewrite instead of deleting it.
- Fix documentation drift by correcting Python version, default model, dependency status, and flag descriptions across CLAUDE.md, COMMANDS.md, and module docstrings. Document the new --push flag for analyse and commit commands.
- Fix artifact commit silently failing when `.gitre/` is listed in `.gitignore` by force-adding staged files, and remove the stale `.gitre/` gitignore entry so the analysis cache and changelog are properly committed.
- Fix upstream tracking lost after git-filter-repo rewrites by restoring the branch's upstream reference when re-adding remotes, preventing VS Code from incorrectly showing "Publish Branch" instead of push/pull controls.
