# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- Initial release of gitre, an AI-powered CLI tool that reconstructs meaningful git commit messages and changelogs by analyzing diffs with Claude, featuring a two-step analyze-then-commit workflow, Keep a Changelog formatting, batch analysis, selective commit filtering, and git history rewriting via git-filter-repo.
- README with prerequisites, Claude Code / Agent SDK integration details, full command reference, and usage examples.
- CLAUDE.md project instructions with workflow reminders and Claude Agent SDK configuration notes.
- COMMANDS.md with full CLI reference covering all subcommands, options, and typical workflow examples.
- Automate the post-rewrite workflow: remotes are now saved and restored around git-filter-repo, artifacts are committed automatically, and a new `--push` flag force-pushes the rewritten branch to the remote.

### Changed
- Progress output (spinners, status messages) is now always shown during analysis, not only with `--verbose`. The `--verbose` flag now adds per-commit hash detail for debugging instead of being required for any feedback.
- git-filter-repo moved from optional to required dependency — it is now installed automatically with gitre. Removed the unused [rewrite] optional extra along with stale tree-sitter entries.
- Stop auto-gitignoring the `.gitre/` directory — the analysis cache is now tracked by git so it survives history rewrites and repo restores.
- Default `--model` changed from `sonnet` to `opus` for higher quality analysis.
- Rewrite README documentation with detailed two-phase pipeline explanation, add Compatibility section for supported Git remotes, and fix several inaccuracies including Python version requirement, default model, and missing --push flag.

### Fixed
- Write filter-repo callback to a temp file instead of passing inline — fixes Windows command-line length limit (WinError 206) on repos with many commits.
- Switch from `--message-callback` to `--commit-callback` with hash-based matching — fixes all commits getting the same message when many share identical original messages (e.g. "etc").
- Fix UnicodeEncodeError crash on Windows (cp1252) by forcing UTF-8 on Rich console output, and stop deleting analysis.json after rewrite so the cache is preserved for safety.
- The `analyze` command now exits with a clear error when `--push` is passed without `--live`, preventing silent misuse of incompatible flags.
- Fix documentation drift by syncing CLAUDE.md, COMMANDS.md, and module docstrings with the actual codebase, correcting the Python version, default model, dependency status, flag descriptions, and adding the missing --push flag documentation.
