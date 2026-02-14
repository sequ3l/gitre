# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- gitre CLI tool with `analyze` and `commit` commands for AI-powered git commit message reconstruction, changelog generation, and optional history rewriting via git-filter-repo.
- README with prerequisites, Claude Code / Agent SDK integration details, full command reference, and usage examples.
- CLAUDE.md project instructions with workflow reminders and Claude Agent SDK configuration notes.
- COMMANDS.md with full CLI reference covering all subcommand arguments, options, and workflow examples for one-shot, two-step, and selective rewrite scenarios.
- Automatic remote save/restore around git-filter-repo, automatic artifact commit after rewrite, and a `--push` flag on `analyze --live` and `commit` to force-push rewritten history to remote.
- The `analyze` command now validates flag combinations and exits with a clear error when `--push` is used without `--live`.

### Changed
- Progress output (spinners, status messages) is now always shown during analysis, not only with `--verbose`. The `--verbose` flag now adds per-commit hash detail for debugging, and batch generation now includes progress spinners.
- `git-filter-repo` moved from optional to required dependency — it is now installed automatically with gitre. Removed unused `[rewrite]` optional-dependencies group and stale tree-sitter entries.
- Stop auto-gitignoring `.gitre/` directory — analysis cache is now tracked by git so it survives history rewrites and repo restores.
- Default `--model` changed from `sonnet` to `opus` for higher quality analysis out of the box.
- Rewrite README with full two-phase pipeline documentation, add Compatibility section for supported Git remotes, and fix several inaccuracies including Python version requirement, default model, and missing --push flag.

### Fixed
- Write filter-repo callback to a temp file instead of passing it inline, fixing Windows command-line length limit (WinError 206) on repos with many commits.
- Switch from `--message-callback` to `--commit-callback` with hash-based matching — fixes all commits getting the same rewritten message when many share identical original messages (e.g. "etc").
- Fix UnicodeEncodeError crash on Windows (cp1252) by forcing UTF-8 encoding on Rich console output, and stop deleting analysis.json after history rewrite so the cache is preserved for safety and re-runs.
- Fix documentation drift by syncing CLAUDE.md, COMMANDS.md, and module docstrings with the actual codebase, correcting the Python version requirement, default model, dependency status, flag descriptions, and adding missing --push flag documentation.
- Fix artifact commit silently failing when `.gitre/` is listed in `.gitignore` by force-adding staged files. Also remove the stale `.gitre/` gitignore entry and include the generated changelog and analysis cache in the commit.
