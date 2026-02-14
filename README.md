# gitre

Reconstruct meaningful git commit messages and changelogs by analyzing diffs with Claude.

Many repositories accumulate lazy commit messages — "etc", "fix", "wip", "update". **gitre** reads the diffs (the source of truth) and uses Claude to generate proper commit messages and changelogs, then optionally rewrites history with the corrected messages.

## Prerequisites

- **Python 3.10+**
- **Claude Code CLI** — gitre calls Claude through the [Claude Agent SDK](https://www.npmjs.com/package/@anthropic-ai/claude-code-sdk), which wraps the Claude Code CLI. You need Claude Code installed and authenticated (either via API key or a Claude Max/Pro subscription).
- **git-filter-repo** *(optional)* — required only for history rewriting (`gitre commit` / `--live`). Install via `pip install git-filter-repo` or your system package manager.

## Installation

```bash
pip install -e ".[dev]"
```

This installs gitre along with its dependencies:
- `typer[all]` — CLI framework with rich output
- `pydantic` — data validation
- `claude-agent-sdk` — Claude Code integration

## How It Works

gitre uses the **Claude Agent SDK** (`claude-agent-sdk`) to call Claude through the Claude Code CLI. When `ANTHROPIC_API_KEY` is not set in your environment, it automatically uses your Claude Max/Pro subscription — meaning no separate API costs.

The SDK is configured with:
- **`bypassPermissions`** mode — gitre only reads diffs, it doesn't write files through Claude
- **`output_format`** JSON schemas — for structured, parseable responses
- **Stripped `ANTHROPIC_API_KEY`** — forces the SDK to use your Max subscription rather than an API key
- **Low `max_turns` (3)** — gitre only needs Claude to read a diff and produce JSON, not run multi-step workflows
- **10 MB buffer** — large diffs need room

Each commit's diff is sent to Claude with a prompt requesting an imperative-mood commit message and a categorized changelog entry. Claude responds with structured JSON that gitre parses into its internal models.

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
| `--model` | Claude model: `sonnet`, `opus`, `haiku` (default: `sonnet`) |
| `--batch-size` | Commits per Claude call (default: 1) |
| `--verbose` / `-v` | Show progress and token usage |

#### `gitre commit [repo_path]`

Applies cached proposals from a previous `gitre analyze`. Does **not** re-call Claude.

| Option | Description |
|---|---|
| `--only` | Comma-separated short hashes to apply |
| `--skip` | Comma-separated short hashes to skip |
| `--changelog` / `-f` | Also write changelog to this path |
| `--yes` / `-y` | Skip confirmation prompt |

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
```

## Safety

Before any history rewrite, gitre:

1. Creates a backup branch (`gitre-backup-{timestamp}`)
2. Prompts for explicit confirmation (unless `-y`)
3. Reminds you that a force push is needed afterward

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
