# gitre - Project Instructions

## Overview
gitre reconstructs meaningful git commit messages and changelogs by analyzing diffs with Claude (via claude-agent-sdk).

## Tech Stack
- Python 3.11+, asyncio
- Typer CLI, Pydantic v2
- claude-agent-sdk for LLM calls
- git-filter-repo for history rewriting (required dependency)

## Commands
- Install: `pip install -e ".[dev]"`
- Test: `pytest tests/ -v`
- Run: `gitre analyze <repo_path>` or `gitre commit <repo_path>`

## Code Style
- Type hints on all functions
- Async/await for I/O operations
- Pydantic models for data structures
- Keep modules focused and small

## Testing
- ALL tests must mock Claude SDK calls — see tests/conftest.py autouse fixture
- NEVER make real claude-agent-sdk `query()` calls in tests
- Mock target: `gitre.generator.query` (NOT `claude_agent_sdk.query`)

## Workflow Reminders
- Always update CHANGELOG.md alongside code changes
- Changelog follows Keep a Changelog format (keepachangelog.com)

## Claude Agent SDK Notes
- Uses `bypassPermissions` mode with `allowed_tools=["Read"]`
- Strip `ANTHROPIC_API_KEY` from env so it uses Max subscription
- `output_format` JSON schemas for structured responses
- `max_turns=3`, `max_buffer_size=10MB`
- Multi-strategy JSON extraction handles markdown fences and prose wrapping
