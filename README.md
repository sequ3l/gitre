# gitre

Reconstruct meaningful git commit messages and changelogs by analyzing diffs with Claude.

Many repositories accumulate lazy commit messages — "etc", "fix", "wip", "update". **gitre** reads the diffs (the source of truth) and uses Claude to generate proper commit messages and changelogs, then optionally rewrites history with the corrected messages.

## Prerequisites

- **Python 3.11+**
- **Claude Code CLI** — gitre calls Claude through the [Claude Agent SDK](https://www.npmjs.com/package/@anthropic-ai/claude-code-sdk), which wraps the Claude Code CLI. You need Claude Code installed and authenticated (either via API key or a Claude Max/Pro subscription).
- **git-filter-repo** — used for history rewriting (`gitre commit` / `--live`). Installed automatically as a dependency.

## Installation

```bash
pip install -e ".[dev]"
```

This installs gitre along with its dependencies:
- `typer[all]` — CLI framework with rich output
- `pydantic` — data validation
- `claude-agent-sdk` — Claude Code integration
- `git-filter-repo` — git history rewriting

## How It Works

gitre operates in two phases: **analyze** (read diffs, call Claude, cache proposals) and **commit** (rewrite history with the improved messages). These can run separately or be chained with `--live`.

### 1. Analyze — Generate proposals

1. **Walk history** — `git log --reverse` extracts every commit in the range (oldest first)
2. **Extract diffs** — Each commit's unified diff and `--stat` are pulled via `git diff`. Root commits diff against the empty tree; merge commits are skipped. Patches over 50 KB are truncated.
3. **Call Claude** — Each diff (or a batch of diffs) is sent to Claude via the [Claude Agent SDK](https://www.npmjs.com/package/@anthropic-ai/claude-code-sdk) with a JSON schema requesting an imperative-mood subject line (max 72 chars), optional body, and a categorized changelog entry (Added/Changed/Fixed/Removed/Deprecated/Security).
4. **Cache results** — Proposals are saved to `.gitre/analysis.json` inside the target repo so you can review before committing.

### 2. Commit — Rewrite history

1. **Create backup** — A branch `gitre-backup-{timestamp}` is created at HEAD so you can always recover.
2. **Save remotes** — All remote URLs are captured (because `git-filter-repo` strips them during rewrite).
3. **Rewrite commits** — `git filter-repo --force --commit-callback` rewrites each commit message. The callback matches commits by their **original hash** (`commit.original_id`), not by message content — so even repos full of identical "etc" messages get the right replacement. The callback script is written to a temp file to avoid Windows command-line length limits.
4. **Restore remotes** — The saved remote URLs are re-added to the repo.
5. **Commit artifacts** — The `.gitre/` cache and any generated changelog are committed with a clean message.
6. **Force-push** (optional) — With `--push`, gitre force-pushes the rewritten history to the remote.

### Claude SDK configuration

gitre calls Claude through the Claude Code CLI. When `ANTHROPIC_API_KEY` is not set in your environment, it automatically uses your Claude Max/Pro subscription — meaning no separate API costs.

- **`bypassPermissions`** mode with `allowed_tools=["Read"]` — gitre only reads diffs, it doesn't write files through Claude
- **`output_format`** JSON schemas — for structured, parseable responses
- **Stripped `ANTHROPIC_API_KEY`** — forces the SDK to use your Max subscription rather than an API key
- **Low `max_turns` (3)** — gitre only needs Claude to read a diff and produce JSON, not run multi-step workflows
- **10 MB buffer** — large diffs need room

### Compatibility

gitre works with any standard Git remote — **GitHub**, **GitLab**, **Azure DevOps**, **Bitbucket**, self-hosted servers, or bare repos. It uses only standard Git operations (`git log`, `git diff`, `git filter-repo`, `git remote`, `git push`) with no platform-specific API calls.

## Usage

### The Full Monty (one shot)

Analyze, rewrite history, and write changelog — all in one go:

```bash
gitre analyze /path/to/repo --live -f CHANGELOG.md
```

### Careful Workflow (two steps)

Review proposals before applying:

```bash
# Step 1: Analyze and review (cached to .gitre/)
gitre analyze /path/to/repo

# Step 2: Apply proposals + write changelog
gitre commit /path/to/repo -f CHANGELOG.md
```

### Commands

#### `gitre analyze <repo_path>`

Walks the commit history, sends each diff to Claude, and generates proposed commit messages and changelog entries. Results are cached to `.gitre/analysis.json` inside the target repo.

| Option | Description |
|---|---|
| `--output` / `-o` | `changelog`, `messages`, or `both` (default: `both`) |
| `--format` | Changelog format: `keepachangelog` (default) |
| `--from` | Starting commit hash or ref (default: root commit) |
| `--to` | Ending commit hash or ref (default: HEAD) |
| `--live` | Immediately rewrite history and write changelog |
| `--out-file` / `-f` | Write changelog to file (e.g. `CHANGELOG.md`) |
| `--model` | Claude model: `sonnet`, `opus`, `haiku` (default: `opus`) |
| `--batch-size` | Commits per Claude call (default: 1) |
| `--verbose` / `-v` | Show per-commit hash details during analysis |
| `--push` | Force-push to remote after rewriting (requires `--live`) |

#### `gitre commit [repo_path]`

Applies cached proposals from a previous `gitre analyze`. Does **not** re-call Claude.

| Option | Description |
|---|---|
| `--only` | Comma-separated short hashes to apply |
| `--skip` | Comma-separated short hashes to skip |
| `--changelog` / `-f` | Also write changelog to this path |
| `--yes` / `-y` | Skip confirmation prompt |
| `--push` | Force-push to remote after rewriting history |

### More Examples

```bash
# Just the changelog
gitre analyze /path/to/repo -o changelog -f CHANGELOG.md

# Just the messages
gitre analyze /path/to/repo -o messages

# Specific commit range
gitre analyze /path/to/repo --from v0.1.0 --to v0.2.0

# Opus for best quality, haiku for speed
gitre analyze /path/to/repo --model opus
gitre analyze /path/to/repo --model haiku --batch-size 10

# Selective apply
gitre commit /path/to/repo --only abc1234,def5678
gitre commit /path/to/repo --skip abc1234

# Scripted / CI
gitre commit /path/to/repo -f CHANGELOG.md -y

# One-shot with force-push
gitre analyze /path/to/repo --live -f CHANGELOG.md --push
```

## Safety

Before any history rewrite, gitre:

1. Creates a backup branch (`gitre-backup-{timestamp}`) — restore with `git reset --hard gitre-backup-*`
2. Saves and restores all remote URLs (filter-repo strips them)
3. Prompts for explicit confirmation (unless `-y`)
4. Never force-pushes unless you explicitly pass `--push`

## Project Structure

```
gitre/
    cli.py          # Typer CLI (analyze + commit commands)
    analyzer.py     # Git history walking, diff extraction
    generator.py    # Claude Agent SDK integration
    models.py       # Pydantic models (CommitInfo, GeneratedMessage, AnalysisResult)
    formatter.py    # Keep a Changelog output formatting
    rewriter.py     # git-filter-repo history rewriting
    cache.py        # .gitre/ cache management
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

All tests mock Claude SDK calls via an autouse fixture — no real API calls are made during testing.

## License

MIT
