# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- MIT LICENSE file for public release.
- GitHub Actions CI workflow running tests, linting (ruff), and type checking (mypy) across Python 3.11–3.13 on Ubuntu and Windows.
- Initial release of gitre CLI with `analyze` and `commit` commands for AI-powered git commit message reconstruction, Keep a Changelog output, cache-based two-step workflow, git-filter-repo history rewriting, and a comprehensive test suite.
- README with prerequisites, Claude Code / Agent SDK integration details, full command reference with option tables, usage examples, safety notes, and project structure overview.
- CLAUDE.md project instructions with workflow reminders and Claude Agent SDK configuration notes.
- COMMANDS.md with full CLI reference covering analyze and commit subcommands, their options, and typical workflow examples.
- Automatic remote save/restore around git-filter-repo, automatic artifact commit after rewrite, and a --push flag on analyze --live and commit to force-push rewritten history to remote.
- The `analyze` command now exits with a clear error when `--push` is used without `--live`, preventing unintended push attempts.

### Changed
- Replace `open(sys.stdout.fileno())` console pattern with `sys.stdout.reconfigure(encoding="utf-8")` + plain `Console()` for cleaner Windows UTF-8 handling.
- Complete type hints in CLI internal helpers (`_build_tags_dict`, `_run_generation`, `_format_output`, `_run_commit_flow`) to use `list[CommitInfo]` instead of bare `list`.
- Progress output (spinners, status messages) is now always shown during analysis instead of requiring `--verbose`. The `--verbose` flag now adds per-commit hash detail for debugging, and batch generation now includes progress spinners that were previously missing.
- git-filter-repo moved from optional to required dependency — it is now installed automatically with gitre. The unused [rewrite] optional extra (including stale tree-sitter entries) has been removed.
- Stop auto-gitignoring `.gitre/` directory — analysis cache is now tracked by git so it survives history rewrites and repo restores.
- Default `--model` changed from `sonnet` to `opus` for higher quality analysis.
- Rewrite README with full two-phase pipeline documentation, compatibility matrix for major Git hosts, corrected CLI defaults (Python 3.11+, --model opus, --push flag), and expanded safety section.
- Rewrite CHANGELOG.md entries for clarity and conciseness, consolidating verbose descriptions, improving formatting consistency, and moving the --push flag validation note from Fixed to Added.

### Fixed
- Write filter-repo callback to a temp file instead of passing it inline on the command line, fixing Windows command-line length limit errors (WinError 206) on repositories with many commits.
- Switch from `--message-callback` to `--commit-callback` with hash-based matching — fixes all commits getting the same message when many share identical original messages (e.g. "etc").
- Fix UnicodeEncodeError crash on Windows (cp1252) by forcing UTF-8 encoding on Rich console output, and stop deleting analysis.json after history rewrite so the cache is preserved for safety and re-runs.
- Fix documentation drift by correcting Python version, default model, dependency status, and flag descriptions across CLAUDE.md, COMMANDS.md, and module docstrings. Document the new --push flag for analyse and commit commands.
- Fix artifact commit failing when `.gitre/` is gitignored by adding `-f` to `git add` in `commit_artifacts()`, and remove the stale `.gitre/` entry from `.gitignore` so the analysis cache and changelog are properly committed.
- Fixed missing upstream tracking after git-filter-repo rewrites; VS Code and other tools now correctly show push/pull instead of "Publish Branch".
