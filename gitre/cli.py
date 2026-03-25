"""Typer CLI entry point for gitre.

Provides two commands:

- ``analyze``: Walk git history, enrich commits, generate messages via Claude,
  cache results, and format/display output.
- ``commit``: Load cached analysis, display proposals, confirm with user, and
  rewrite git history using git-filter-repo.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import cast

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from gitre import analyzer, cache, formatter, generator, labeler, rewriter
from gitre.generator import EffortLevel
from gitre.models import AnalysisResult, CommitInfo, GeneratedMessage

app = typer.Typer(
    name="gitre",
    help="AI-powered Git assistant — reconstruct meaningful commit messages and changelogs.",
    add_completion=False,
)

# Ensure UTF-8 console output on Windows (prevents cp1252 encoding errors)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_console = Console()


# ---------------------------------------------------------------------------
# Output format enum for --output / -o
# ---------------------------------------------------------------------------


class OutputFormat(StrEnum):
    """Selects which sections to include in the formatted output."""

    changelog = "changelog"
    messages = "messages"
    both = "both"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_git_repo(repo_path: str) -> None:
    """Raise :class:`typer.Exit` if *repo_path* is not a valid git repository."""
    path = Path(repo_path)
    if not path.exists():
        typer.echo(f"Error: path does not exist: {repo_path}", err=True)
        raise typer.Exit(1)
    if not path.is_dir():
        typer.echo(f"Error: path is not a directory: {repo_path}", err=True)
        raise typer.Exit(1)

    # Quick git sanity check
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            typer.echo(f"Error: not a git repository: {repo_path}", err=True)
            raise typer.Exit(1)
    except FileNotFoundError:
        typer.echo("Error: git is not installed or not on PATH.", err=True)
        raise typer.Exit(1)


def _get_head_hash(repo_path: str) -> str:
    """Return the current HEAD hash for *repo_path*."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _build_tags_dict(commits: list[CommitInfo]) -> dict[str, str]:
    """Build a ``{hash: tag}`` dict from commits that carry version tags."""
    tags: dict[str, str] = {}
    for c in commits:
        for tag in c.tags:
            tags[c.hash] = tag
    return tags


# ---------------------------------------------------------------------------
# analyze command
# ---------------------------------------------------------------------------


@app.command()
def analyze(
    repo_path: str = typer.Argument(".", help="Path to the git repository to analyse."),
    output: OutputFormat = typer.Option(
        OutputFormat.both,
        "--output",
        "-o",
        help="What to output: changelog, messages, or both.",
    ),
    format: str = typer.Option(  # noqa: A002
        "keepachangelog",
        "--format",
        help="Changelog format style.",
    ),
    from_ref: str | None = typer.Option(
        None,
        "--from",
        help="Exclusive start ref (tag or commit hash).",
    ),
    to_ref: str | None = typer.Option(
        None,
        "--to",
        help="Inclusive end ref (defaults to HEAD).",
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="After analysis, immediately run the commit (rewrite) flow.",
    ),
    out_file: str | None = typer.Option(
        None,
        "--out-file",
        "-f",
        help="Write formatted output to this file.",
    ),
    effort: str = typer.Option(
        "max",
        "--effort",
        help="Thinking effort level: low, medium, high, or max.",
    ),
    batch_size: int = typer.Option(
        0,
        "--batch-size",
        help="Commits per Claude call. 0 = auto-batch by diff size (default).",
        min=0,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress information.",
    ),
    push: bool = typer.Option(
        False,
        "--push",
        help="Force-push to remote after rewriting (requires --live).",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "-i",
        help="Interactively review each proposal before rewriting (requires --live).",
    ),
) -> None:
    """Analyse git history and generate improved commit messages + changelog."""
    # --- 0. Validate flag combinations ---
    if push and not live:
        typer.echo("Error: --push requires --live.", err=True)
        raise typer.Exit(1)
    if interactive and not live:
        typer.echo("Error: --interactive requires --live.", err=True)
        raise typer.Exit(1)

    # --- 1. Validate repo ---
    _validate_git_repo(repo_path)

    # --- 2. Get commits ---
    _console.print(f"[cyan]Fetching commits from[/cyan] {repo_path} …")

    try:
        commits = analyzer.get_commits(repo_path, from_ref=from_ref, to_ref=to_ref)
    except subprocess.CalledProcessError as exc:
        typer.echo(f"Error fetching commits: {exc}", err=True)
        raise typer.Exit(1)

    if not commits:
        typer.echo("No commits found in the specified range.")
        raise typer.Exit(0)

    _console.print(f"[cyan]Found {len(commits)} commit(s).[/cyan]")

    # --- 3. Enrich each commit ---
    enriched = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=_console,
    ) as progress:
        task = progress.add_task("Enriching commits…", total=len(commits))
        for c in commits:
            enriched.append(analyzer.enrich_commit(repo_path, c))
            progress.advance(task)

    # --- 4. Check for resumable cache ---
    head_hash = _get_head_hash(repo_path)
    tags = _build_tags_dict(enriched)
    cached_result, done_hashes = cache.can_resume(
        repo_path, head_hash, from_ref, to_ref,
    )

    if done_hashes:
        cached_messages = list(cached_result.messages)  # type: ignore[union-attr]
        remaining = [c for c in enriched if c.hash not in done_hashes]
        _console.print(
            f"[green]Resuming: {len(done_hashes)} commit(s) cached, "
            f"{len(remaining)} remaining.[/green]"
        )
    else:
        cached_messages = []
        remaining = enriched

    # --- 5. Generate messages (or skip if all cached) ---
    if not remaining:
        _console.print("[green]All commits already analyzed. Using cached results.[/green]")
        assert cached_result is not None  # guaranteed when done_hashes is non-empty
        result = cached_result
        all_messages = list(result.messages)
    else:
        _console.print("[cyan]Generating messages via Claude…[/cyan]")

        def _save_progress(new_messages: list[GeneratedMessage]) -> None:
            """Save incremental progress after each commit/batch."""
            merged = cached_messages + new_messages
            partial = AnalysisResult(
                repo_path=repo_path,
                head_hash=head_hash,
                from_ref=from_ref,
                to_ref=to_ref,
                commits_analyzed=len(merged),
                messages=merged,
                tags=tags,
            )
            cache.save_analysis(repo_path, partial)

        try:
            new_messages = _run_generation(
                remaining, repo_path, batch_size, verbose,
                effort=cast(EffortLevel, effort), on_progress=_save_progress,
            )
        except Exception as exc:
            _console.print(
                f"[red]Error during analysis: {exc}[/red]\n"
                f"[yellow]Progress saved ({len(cached_messages)} + partial). "
                f"Re-run to resume.[/yellow]"
            )
            raise typer.Exit(1)

        all_messages = cached_messages + new_messages
        result = AnalysisResult(
            repo_path=repo_path,
            head_hash=head_hash,
            from_ref=from_ref,
            to_ref=to_ref,
            commits_analyzed=len(all_messages),
            messages=all_messages,
            tags=tags,
        )

    # --- 6. Save to cache ---
    cache.save_analysis(repo_path, result)
    _console.print("[green]Analysis saved to cache.[/green]")

    # --- 7. Format output ---
    formatted = _format_output(output, all_messages, enriched, tags, format)

    # --- 8. Display output ---
    typer.echo(formatted)

    # --- 9. Write to file (optional) ---
    if out_file:
        out_path = Path(out_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(formatted, encoding="utf-8")
        _console.print(f"[green]Output written to {out_file}[/green]")

    # --- 10. If --live, also run commit flow ---
    if live:
        _run_commit_flow(
            repo_path, result, enriched,
            yes=False, changelog_file=out_file, push=push,
            interactive=interactive,
        )


# ---------------------------------------------------------------------------
# commit command
# ---------------------------------------------------------------------------


@app.command()
def commit(
    repo_path: str = typer.Argument(
        ".",
        help="Path to the git repository (defaults to current directory).",
    ),
    only: str | None = typer.Option(
        None,
        "--only",
        help="Comma-separated list of short hashes to include (others skipped).",
    ),
    skip: str | None = typer.Option(
        None,
        "--skip",
        help="Comma-separated list of short hashes to skip.",
    ),
    changelog: str | None = typer.Option(
        None,
        "--changelog",
        "-f",
        help="Write changelog to this file path after rewriting.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt.",
    ),
    push: bool = typer.Option(
        False,
        "--push",
        help="Force-push to remote after rewriting history.",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "-i",
        help="Interactively review each proposal (accept/skip/edit).",
    ),
) -> None:
    """Load cached analysis and rewrite git history with improved messages."""
    # --- 1. Validate repo ---
    _validate_git_repo(repo_path)

    # --- 2. Load cache ---
    try:
        result = cache.load_analysis(repo_path)
    except FileNotFoundError:
        typer.echo(
            "Error: no cached analysis found. Run 'gitre analyze' first.",
            err=True,
        )
        raise typer.Exit(1)
    except Exception as exc:
        typer.echo(f"Error loading cached analysis: {exc}", err=True)
        raise typer.Exit(1)

    # --- 3. Validate cache (warn if stale) ---
    is_valid, warning = cache.validate_cache(repo_path, result)
    if not is_valid:
        typer.echo(f"Warning: {warning}", err=True)

    # --- 4. Apply only/skip filters ---
    messages = list(result.messages)

    if only:
        only_set = {h.strip() for h in only.split(",") if h.strip()}
        messages = [m for m in messages if m.short_hash in only_set]

    if skip:
        skip_set = {h.strip() for h in skip.split(",") if h.strip()}
        messages = [m for m in messages if m.short_hash not in skip_set]

    if not messages:
        typer.echo("No commits to rewrite after applying filters.")
        raise typer.Exit(0)

    # --- 5-8. Run the commit flow with filtered messages ---
    # --yes overrides --interactive
    effective_interactive = interactive and not yes
    _run_commit_flow(
        repo_path,
        result,
        commits=None,
        yes=yes,
        changelog_file=changelog,
        filtered_messages=messages,
        push=push,
        interactive=effective_interactive,
    )


# ---------------------------------------------------------------------------
# label command
# ---------------------------------------------------------------------------


@app.command()
def label(
    repo_path: str = typer.Argument(
        ".",
        help="Path to the git repository (defaults to current directory).",
    ),
    all_changes: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Stage all changes before generating (like git commit -a).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt.",
    ),
    push: bool = typer.Option(
        False,
        "--push",
        help="Push to remote after committing.",
    ),
    effort: str = typer.Option(
        "max",
        "--effort",
        help="Thinking effort level: low, medium, high, or max.",
    ),
) -> None:
    """Generate a commit message for staged changes and commit."""
    # --- 1. Validate repo ---
    _validate_git_repo(repo_path)

    # --- 2. Stage all if requested ---
    if all_changes:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    # --- 3. Check for staged changes ---
    status = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    if status.returncode == 0:
        typer.echo("No staged changes to label.")
        raise typer.Exit(0)

    # --- 4. Generate label ---
    _console.print("[cyan]Generating commit message for staged changes…[/cyan]")

    async def _gen() -> GeneratedMessage:
        return await labeler.generate_label(repo_path, effort=cast(EffortLevel, effort))

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=_console,
        ) as progress:
            progress.add_task("Analysing…", total=None)
            msg = asyncio.run(_gen())
    except RuntimeError as exc:
        typer.echo(f"Error generating label: {exc}", err=True)
        raise typer.Exit(1)

    # --- 5. Display proposal ---
    _console.print()
    _console.print(f"[green bold]Subject:[/green bold] {msg.subject}")
    if msg.body:
        _console.print(f"[dim]Body:[/dim]    {msg.body}")
    _console.print(
        f"[magenta]Category:[/magenta] {msg.changelog_category}"
    )
    _console.print()

    # --- 6. Confirm ---
    if not yes:
        if not typer.confirm("Commit with this message?", default=True):
            typer.echo("Aborted.")
            raise typer.Exit(0)

    # --- 7. Commit ---
    message = msg.subject
    if msg.body:
        message = f"{msg.subject}\n\n{msg.body}"

    try:
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        _console.print(f"[green]Committed:[/green] {msg.subject}")
    except subprocess.CalledProcessError as exc:
        typer.echo(f"Error committing: {exc}", err=True)
        raise typer.Exit(1)

    # --- 8. Push (optional) ---
    if push:
        try:
            subprocess.run(
                ["git", "push"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            _console.print("[green]Pushed to remote.[/green]")
        except subprocess.CalledProcessError as exc:
            typer.echo(f"Error pushing: {exc}", err=True)
            raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


# Auto-batch budget: max total diff chars per batch
_AUTO_BATCH_BUDGET = 400_000
# Quality cap: max commits per batch regardless of diff size
_AUTO_BATCH_MAX_COMMITS = 20


def _auto_batch_commits(commits: list[CommitInfo]) -> list[list[CommitInfo]]:
    """Group commits into batches that fit within the diff size budget.

    Keeps adding commits to the current batch until total diff chars
    would exceed the budget or the commit count cap is reached, then
    starts a new batch.  A single commit that exceeds the budget on
    its own goes in a solo batch.
    """
    batches: list[list[CommitInfo]] = []
    current_batch: list[CommitInfo] = []
    current_size = 0

    for commit in commits:
        # Account for diff + stat + ~500 chars per-commit prompt overhead
        commit_size = len(commit.diff_patch) + len(commit.diff_stat) + 500

        if current_batch and (
            current_size + commit_size > _AUTO_BATCH_BUDGET
            or len(current_batch) >= _AUTO_BATCH_MAX_COMMITS
        ):
            batches.append(current_batch)
            current_batch = [commit]
            current_size = commit_size
        else:
            current_batch.append(commit)
            current_size += commit_size

    if current_batch:
        batches.append(current_batch)

    return batches


def _run_generation(
    enriched: list[CommitInfo],
    repo_path: str,
    batch_size: int,
    verbose: bool,
    *,
    effort: EffortLevel = "max",
    on_progress: Callable[[list[GeneratedMessage]], None] | None = None,
) -> list[GeneratedMessage]:
    """Drive message generation, single or batch, via asyncio.run().

    When *batch_size* is ``0`` (the default), commits are grouped
    automatically by diff size using :func:`_auto_batch_commits`.
    """
    messages: list[GeneratedMessage] = []

    if batch_size == 1:
        # Individual generation — always show progress
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=_console,
        ) as progress:
            task = progress.add_task("Generating…", total=len(enriched))
            cumulative_cost = 0.0

            async def _generate_singles() -> list[GeneratedMessage]:
                nonlocal cumulative_cost
                results: list[GeneratedMessage] = []
                for c in enriched:
                    if verbose:
                        _console.print(f"  [dim]{c.short_hash}[/dim] {c.original_message[:50]}")
                    msg, _tokens, cost = await generator.generate_message(
                        c, cwd=repo_path, effort=effort,
                    )
                    results.append(msg)
                    cumulative_cost += cost
                    progress.update(
                        task, description=f"Generating… ${cumulative_cost:.2f}",
                    )
                    progress.advance(task)
                    if on_progress:
                        on_progress(list(results))
                return results

            messages = asyncio.run(_generate_singles())
            _console.print(
                f"[green]Generated {len(messages)} message(s)"
                f" — ${cumulative_cost:.2f} total[/green]"
            )
    else:
        # Batch generation (auto or fixed)
        if batch_size == 0:
            batches = _auto_batch_commits(enriched)
        else:
            batches = [
                enriched[i : i + batch_size]
                for i in range(0, len(enriched), batch_size)
            ]

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=_console,
        ) as progress:
            task = progress.add_task("Generating batches…", total=len(batches))
            cumulative_cost = 0.0

            async def _generate_batches() -> list[GeneratedMessage]:
                nonlocal cumulative_cost
                all_messages: list[GeneratedMessage] = []
                for batch_idx, batch in enumerate(batches, start=1):
                    if verbose:
                        hashes = ", ".join(c.short_hash for c in batch)
                        _console.print(f"  [dim]Batch {batch_idx}: {hashes}[/dim]")
                    batch_result = await generator.generate_messages_batch(
                        batch, cwd=repo_path, effort=effort,
                    )
                    all_messages.extend(batch_result.messages)
                    cumulative_cost += batch_result.total_cost
                    progress.update(
                        task,
                        description=(
                            f"Generating… batch {batch_idx}/{len(batches)} "
                            f"({len(batch)} commits) ${cumulative_cost:.2f}"
                        ),
                    )
                    progress.advance(task)
                    if on_progress:
                        on_progress(list(all_messages))
                return all_messages

            messages = asyncio.run(_generate_batches())
            _console.print(
                f"[green]Generated {len(messages)} message(s)"
                f" — ${cumulative_cost:.2f} total[/green]"
            )

    return messages


def _format_output(
    output: OutputFormat,
    messages: list[GeneratedMessage],
    commits: list[CommitInfo],
    tags: dict[str, str],
    format_style: str,
) -> str:
    """Format analysis output according to the requested output type."""
    if output == OutputFormat.changelog:
        return formatter.format_changelog(messages, tags, format_style=format_style)
    elif output == OutputFormat.messages:
        return formatter.format_messages(messages, commits)
    else:
        return formatter.format_both(messages, commits, tags)


def _interactive_review(
    messages: list[GeneratedMessage],
    commits: list[CommitInfo] | None = None,
) -> list[GeneratedMessage]:
    """Interactively review each proposal — accept, skip, or edit.

    Returns the list of accepted (possibly edited) messages.
    """
    # Build a lookup for original messages
    originals: dict[str, str] = {}
    if commits:
        originals = {c.short_hash: c.original_message for c in commits}

    accepted: list[GeneratedMessage] = []
    for idx, msg in enumerate(messages, start=1):
        original = originals.get(msg.short_hash, "?")
        _console.print()
        _console.print(
            f"[bold][{idx}/{len(messages)}][/bold] "
            f"[dim]{msg.short_hash}[/dim]  "
            f'[red]"{original}"[/red] → [green]"{msg.subject}"[/green]'
        )
        if msg.body:
            _console.print(f"  [dim]Body:[/dim] {msg.body}")
        _console.print(f"  [magenta]Category:[/magenta] {msg.changelog_category}")

        while True:
            choice = typer.prompt(
                "  (a)ccept / (s)kip / (e)dit subject / (q)uit",
                default="a",
                show_default=False,
            ).strip().lower()

            if choice in ("a", ""):
                accepted.append(msg)
                _console.print("  [green]Accepted[/green]")
                break
            elif choice == "s":
                _console.print("  [yellow]Skipped[/yellow]")
                break
            elif choice == "e":
                new_subject = typer.prompt("  New subject", default=msg.subject)
                if len(new_subject) > 72:
                    new_subject = new_subject[:69] + "..."
                msg = msg.model_copy(update={"subject": new_subject})
                accepted.append(msg)
                _console.print(f"  [green]Accepted (edited):[/green] {new_subject}")
                break
            elif choice == "q":
                _console.print("  [yellow]Stopping — applying accepted so far.[/yellow]")
                return accepted
            else:
                _console.print("  [red]Invalid choice. Use a/s/e/q.[/red]")

    return accepted


def _run_commit_flow(
    repo_path: str,
    result: AnalysisResult,
    commits: list[CommitInfo] | None,
    *,
    yes: bool,
    changelog_file: str | None,
    filtered_messages: list[GeneratedMessage] | None = None,
    push: bool = False,
    interactive: bool = False,
) -> None:
    """Shared commit/rewrite flow used by both the commit command and --live flag.

    Steps:
      1. Display proposals (or interactive review)
      2. Confirm (unless -y or interactive)
      3. Check filter-repo availability
      4. Rewrite history (saves/restores remotes internally)
      5. Optionally write changelog
      6. Commit artifacts (changelog + analysis cache)
      7. Report results
      8. Force push (if --push)
    """
    messages = list(filtered_messages) if filtered_messages is not None else list(result.messages)

    if not messages:
        typer.echo("No messages to rewrite.")
        return

    if interactive:
        # Interactive review replaces display + confirm
        messages = _interactive_review(messages, commits)
        if not messages:
            typer.echo("No proposals accepted. Aborted.")
            raise typer.Exit(0)
        _console.print(f"\n[cyan]{len(messages)} proposal(s) accepted.[/cyan]")
    else:
        # 1. Display proposals
        rewriter.display_proposals(messages, commits)

        # 2. Confirm
        if not yes:
            if not rewriter.confirm_rewrite():
                typer.echo("Aborted.")
                raise typer.Exit(0)

    # 3. Check filter-repo
    if not rewriter.check_filter_repo():
        instructions = rewriter.get_install_instructions()
        typer.echo(f"Error: git-filter-repo is not installed.\n{instructions}", err=True)
        raise typer.Exit(1)

    # 4. Rewrite history (creates backup internally)
    try:
        results_map = rewriter.rewrite_history(repo_path, messages)
    except subprocess.CalledProcessError as exc:
        typer.echo(f"Error during history rewrite: {exc}", err=True)
        raise typer.Exit(1)
    except (RuntimeError, SystemExit) as exc:
        typer.echo(f"Error during history rewrite: {exc}", err=True)
        raise typer.Exit(1)

    # 5. Optionally write changelog
    if changelog_file:
        changelog_content = formatter.format_changelog(
            messages, result.tags
        )
        rewriter.write_changelog(repo_path, changelog_content, changelog_file)

    # 6. Commit artifacts (changelog + analysis cache)
    try:
        rewriter.commit_artifacts(repo_path, changelog_file=changelog_file)
    except subprocess.CalledProcessError as exc:
        typer.echo(f"Warning: failed to commit artifacts: {exc}", err=True)

    # 7. Report results
    typer.echo(f"\nSuccessfully rewrote {len(results_map)} commit(s).")
    for short_hash, description in results_map.items():
        typer.echo(f"  {short_hash}: {description}")

    # 8. Force push (opt-in)
    if push:
        try:
            rewriter.force_push(repo_path)
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            typer.echo(f"Error during push: {exc}", err=True)
            raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
