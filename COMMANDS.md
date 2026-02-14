# gitre Commands

## `gitre analyze <repo_path>`

Analyse git history and generate improved commit messages + changelog.

### Arguments

| Argument | Description |
|---|---|
| `repo_path` | Path to the git repository to analyse. **Required.** |

### Options

| Option | Short | Default | Description |
|---|---|---|---|
| `--output` | `-o` | `both` | What to output: `changelog`, `messages`, or `both` |
| `--format` | | `keepachangelog` | Changelog format style |
| `--from` | | root commit | Exclusive start ref (tag or commit hash) |
| `--to` | | `HEAD` | Inclusive end ref |
| `--live` | | | After analysis, immediately rewrite history and write changelog |
| `--out-file` | `-f` | | Write formatted output to this file |
| `--model` | | `opus` | Claude model to use: `sonnet`, `opus`, `haiku` |
| `--batch-size` | | `1` | Commits to analyse per Claude call (higher = faster, possibly less accurate) |
| `--verbose` | `-v` | | Show per-commit hash details during analysis |
| `--push` | | | Force-push to remote after rewriting (requires `--live`) |

### Examples

```bash
# Full analysis with both messages and changelog
gitre analyze /path/to/repo

# One-shot: analyze, rewrite history, and write changelog
gitre analyze /path/to/repo --live -f CHANGELOG.md

# Just the changelog
gitre analyze /path/to/repo -o changelog -f CHANGELOG.md

# Just the messages
gitre analyze /path/to/repo -o messages

# Specific commit range
gitre analyze /path/to/repo --from v0.1.0 --to v0.2.0

# Use Opus for best quality
gitre analyze /path/to/repo --model opus

# Use Haiku with batching for speed
gitre analyze /path/to/repo --model haiku --batch-size 10

# Verbose output with token usage
gitre analyze /path/to/repo -v
```

---

## `gitre commit [repo_path]`

Load cached analysis and rewrite git history with improved messages. Does **not** re-call Claude — uses results cached by a previous `gitre analyze`.

### Arguments

| Argument | Description |
|---|---|
| `repo_path` | Path to the git repository. Defaults to current directory. |

### Options

| Option | Short | Default | Description |
|---|---|---|---|
| `--only` | | | Comma-separated list of short hashes to include (others skipped) |
| `--skip` | | | Comma-separated list of short hashes to skip |
| `--changelog` | `-f` | | Write changelog to this file path after rewriting |
| `--yes` | `-y` | | Skip confirmation prompt (for scripting / CI) |
| `--push` | | | Force-push to remote after rewriting history |

### Examples

```bash
# Apply all cached proposals
gitre commit /path/to/repo

# Apply and write changelog
gitre commit /path/to/repo -f CHANGELOG.md

# Only apply specific commits
gitre commit /path/to/repo --only abc1234,def5678

# Skip specific commits
gitre commit /path/to/repo --skip abc1234

# Non-interactive (scripted / CI)
gitre commit /path/to/repo -f CHANGELOG.md -y

# Non-interactive with force-push
gitre commit /path/to/repo -f CHANGELOG.md -y --push
```

---

## Typical Workflows

### The Full Monty (one shot)

```bash
gitre analyze /path/to/repo --live -f CHANGELOG.md
```

### Two-Step (review first)

```bash
# Step 1: Analyze and review proposals
gitre analyze /path/to/repo

# Step 2: Apply proposals
gitre commit /path/to/repo -f CHANGELOG.md
```

### Selective Rewrite

```bash
# Analyze everything
gitre analyze /path/to/repo

# Only rewrite the commits you like
gitre commit /path/to/repo --only abc1234,def5678
```
