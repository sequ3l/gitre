# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- Add incremental save and resume to `gitre analyze` — progress is saved after each commit/batch so interrupted runs (rate limits, crashes, Ctrl+C) can be resumed by re-running the same command. Shows how many commits are cached vs. remaining.
- Add native OS installers to the release workflow: Windows installer (Inno Setup), macOS `.pkg`, and Linux `.deb`/`.rpm` packages. Installers handle PATH setup automatically. Standalone binaries are still included alongside the installers.
- Initial release of gitre CLI with analyze and commit commands for AI-powered git commit message generation, Keep a Changelog formatting, cache management, and git history rewriting via git-filter-repo.
- README with prerequisites, Claude Code / Agent SDK integration details, full command reference with option tables, usage examples, safety notes, and project structure overview.
- CLAUDE.md project instructions with workflow reminders and Claude Agent SDK configuration notes.
- COMMANDS.md with full CLI reference covering analyze and commit subcommands, their options, and typical workflow examples.
- Add automatic remote save/restore around git-filter-repo, artifact commit after rewrite, and --push flag for force-pushing rewritten history to remote.
- Add input validation to the `analyze` command that rejects `--push` when `--live` is not also specified, exiting early with a clear error message.
- Add GitHub Actions CI workflow (tests, ruff, mypy across Python 3.11–3.13 on Ubuntu/Windows) and MIT LICENSE file for public release. Improve CLI code quality with precise type hints and cleaner Windows UTF-8 console handling.
- Add `gitre label` command to generate AI-powered commit messages for staged changes and commit in one step, with `--all`, `--push`, and `--model` options.
- Added CI status, Python version, and MIT license badges to the README for quick project overview.

### Changed
- Progress output (spinners, status messages) is now always shown during analysis instead of requiring `--verbose`. The `--verbose` flag now adds per-commit hash detail for debugging, and batch generation now includes progress spinners that were previously missing.
- `git-filter-repo` moved from optional to required dependency — installed automatically with gitre. Removed stale `tree-sitter` entries from the `[rewrite]` optional-dependencies group.
- Stop auto-gitignoring `.gitre/` directory — analysis cache is now tracked by git so it survives history rewrites and repo restores.
- Default `--model` changed from `sonnet` to `opus` for higher quality analysis.
- Rewrite README with full two-phase pipeline documentation, compatibility matrix for major Git hosts, corrected CLI defaults (Python 3.11+, --model opus, --push flag), and expanded safety notes.
- Rewrite CHANGELOG entries for conciseness and accuracy: consolidate verbose bullet points, improve formatting consistency, add missing details about --push validation and artifact commit fixes, and promote flag-validation entry from Fixed to Added.
- Rewrite CHANGELOG.md entries for clarity and conciseness, consolidating verbose descriptions, improving formatting consistency, adding a missing upstream tracking fix entry, and recategorizing the --push flag validation note from Fixed to Added.
- Rewrite CHANGELOG entries for conciseness and accuracy: consolidate verbose bullet points, improve formatting consistency, add missing details about --push validation and artifact commit behavior, and reorganize entries across Added/Changed/Fixed sections.

### Removed
- Remove `.gitre/analysis.json` from version control — generated analysis cache artifacts are no longer tracked in the repository.

### Fixed
- Write filter-repo callback to a temp file instead of passing it inline, fixing Windows command-line length limit (WinError 206) on repos with many commits.
- Switch from `--message-callback` to `--commit-callback` with hash-based matching — fixes all commits getting the same rewritten message when many share identical original messages (e.g. "etc").
- Fix UnicodeEncodeError crash on Windows (cp1252) by forcing UTF-8 encoding on Rich console output, and stop deleting analysis.json after history rewrite so the cache is preserved for safety and re-runs.
- Fix documentation drift by correcting Python version (3.11+), default model (opus), git-filter-repo dependency status, and CLI flag descriptions across CLAUDE.md, COMMANDS.md, and module docstrings. Document the new --push flag for analyse and commit commands.
- Fix artifact commit failing when `.gitre/` is gitignored by adding `-f` to `git add` in `commit_artifacts()`, and remove the stale `.gitre/` entry from `.gitignore` so the analysis cache and changelog are committed correctly.
- Fix lost upstream tracking after git-filter-repo rewrites by automatically restoring the branch's upstream reference when remotes are re-added.
- Fix CLI help text test failures caused by ANSI escape sequences in Rich-styled output by stripping colour codes before assertions.
- Suppress mypy 'import-untyped' error for the git_filter_repo import in check_filter_repo() to allow clean type-checking runs.
- Fix `restore_remotes` crashing with exit status 3 when remotes already exist by falling back to `git remote set-url`. Stop tracking the `.gitre/` analysis cache in git to prevent interference with git-filter-repo history rewrites.

## [v0.0.1]

### Changed
- Refactor git-filter-repo integration from subprocess calls to the Python library API (`RepoFilter`), bundling it so users no longer need to install git-filter-repo separately. Add GitHub Actions release workflow and PyInstaller spec for building standalone executables on Windows, Linux, and macOS.
