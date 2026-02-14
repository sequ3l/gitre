# gitre - Project Instructions

## Overview
gitre reconstructs meaningful git commit messages and changelogs by analyzing diffs with Claude (via claude-agent-sdk).

## Tech Stack
- Python 3.10+, asyncio
- Typer CLI, Pydantic v2
- claude-agent-sdk for LLM calls
- git-filter-repo for history rewriting (optional dependency)

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
