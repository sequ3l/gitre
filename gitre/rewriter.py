"""Git history rewriting using git-filter-repo.

Provides functions to rewrite commit messages in a git repository using
git-filter-repo's --commit-callback mechanism with hash-based matching.
Includes backup creation, remote save/restore, user confirmation, changelog
generation, and rich console display of proposals.
"""

from __future__ import annotations

import platform
import subprocess
import tempfile
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gitre.models import CommitInfo, GeneratedMessage

# ---------------------------------------------------------------------------
# Module-level console instance used by display helpers
# ---------------------------------------------------------------------------
_console = Console()


# ---------------------------------------------------------------------------
# 1. check_filter_repo
# ---------------------------------------------------------------------------
def check_filter_repo() -> bool:
    """Check whether ``git-filter-repo`` is available on the system PATH.

    Returns ``True`` if the tool can be invoked, ``False`` otherwise.
    """
    try:
        result = subprocess.run(
            ["git", "filter-repo", "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


# ---------------------------------------------------------------------------
# 2. get_install_instructions
# ---------------------------------------------------------------------------
def get_install_instructions() -> str:
    """Return platform-appropriate install instructions for *git-filter-repo*.

    Detects the current OS via :mod:`platform` and provides the most common
    installation method for that platform.
    """
    system = platform.system().lower()

    if system == "darwin":
        return textwrap.dedent("""\
            Install git-filter-repo on macOS:
              brew install git-filter-repo
            Or via pip:
              pip install git-filter-repo""")
    elif system == "linux":
        return textwrap.dedent("""\
            Install git-filter-repo on Linux:
              # Debian / Ubuntu
              sudo apt-get install git-filter-repo
              # Fedora
              sudo dnf install git-filter-repo
              # Arch
              sudo pacman -S git-filter-repo
            Or via pip:
              pip install git-filter-repo""")
    else:
        # Windows or other
        return textwrap.dedent("""\
            Install git-filter-repo on Windows:
              pip install git-filter-repo
            Or via scoop:
              scoop install git-filter-repo""")


# ---------------------------------------------------------------------------
# 3. create_backup
# ---------------------------------------------------------------------------
def create_backup(repo_path: str) -> str:
    """Create a backup branch named ``gitre-backup-{timestamp}``.

    Parameters
    ----------
    repo_path:
        Path to the root of the git repository.

    Returns
    -------
    str
        The name of the newly created backup branch.

    Raises
    ------
    subprocess.CalledProcessError
        If the ``git branch`` command fails.
    """
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    branch_name = f"gitre-backup-{timestamp}"

    subprocess.run(
        ["git", "branch", branch_name],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return branch_name


# ---------------------------------------------------------------------------
# 4. build_message_callback
# ---------------------------------------------------------------------------
def build_message_callback(messages: list[GeneratedMessage]) -> str:
    """Generate a Python callback script for ``--message-callback``.

    The callback maps original commit messages to new messages by matching
    on the original message content (since commit hashes change during
    rewrite).  The *messages* list must carry the ``hash`` field so that
    we can look up the original commit message for each entry, but the
    actual matching in the callback is done by message content, not hash.

    .. note::

        When called directly (without original messages resolved), this
        returns an identity callback (``return message``).  The
        :func:`rewrite_history` function resolves original messages from
        git and calls :func:`_build_content_callback_with_originals`
        to produce the real mapping callback.

    Parameters
    ----------
    messages:
        List of :class:`GeneratedMessage` objects that define the
        subject (and optional body) for each commit to be rewritten.

    Returns
    -------
    str
        A Python expression/script suitable for passing to
        ``git filter-repo --message-callback``.
    """
    # Without knowing the original commit messages we cannot build the
    # content-based lookup map.  The full pipeline (rewrite_history)
    # resolves originals from git and uses _build_content_callback_with_originals.
    # As a standalone call, return a safe identity callback.
    return "return message"


def _build_commit_callback(
    hash_map: dict[str, str],
) -> str:
    """Build a ``--commit-callback`` script that matches by original hash.

    Unlike ``--message-callback`` (which only receives the message text),
    ``--commit-callback`` receives the full commit object including
    ``commit.original_id`` — the hex hash from before the rewrite.  This
    avoids the duplicate-key problem when many commits share the same
    original message (e.g. "etc").

    Parameters
    ----------
    hash_map:
        Mapping of ``{original_full_hash: new_message}``.

    Returns
    -------
    str
        Python code for ``--commit-callback``.
    """
    mapping_entries: list[str] = []
    for old_hash, new_msg in hash_map.items():
        hash_repr = repr(old_hash)
        msg_repr = repr(new_msg)
        mapping_entries.append(f"  {hash_repr}: {msg_repr},")

    mapping_block = "\n".join(mapping_entries)

    # commit.original_id is a bytes hex hash; decode to str for lookup.
    # commit.message is bytes; we replace it with encoded new message.
    callback = (
        "HASH_MAP = {\n"
        f"{mapping_block}\n"
        "}\n"
        "orig_hex = commit.original_id.decode('ascii') "
        "if isinstance(commit.original_id, bytes) "
        "else str(commit.original_id)\n"
        "if orig_hex in HASH_MAP:\n"
        "    commit.message = HASH_MAP[orig_hex].encode('utf-8') + b'\\n'\n"
    )
    return callback


# ---------------------------------------------------------------------------
# 5. save_remotes / restore_remotes
# ---------------------------------------------------------------------------
def save_remotes(repo_path: str) -> dict[str, str]:
    """Capture all remote URLs before git-filter-repo strips them.

    Returns a ``{name: url}`` dict parsed from ``git remote -v`` fetch lines.
    """
    result = subprocess.run(
        ["git", "remote", "-v"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    remotes: dict[str, str] = {}
    for line in result.stdout.strip().splitlines():
        if "(fetch)" not in line:
            continue
        parts = line.split("\t", 1)
        if len(parts) == 2:
            name = parts[0].strip()
            url = parts[1].replace("(fetch)", "").strip()
            remotes[name] = url
    return remotes


def restore_remotes(repo_path: str, remotes: dict[str, str]) -> None:
    """Re-add remotes that were stripped by git-filter-repo."""
    for name, url in remotes.items():
        subprocess.run(
            ["git", "remote", "add", name, url],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
    if remotes:
        _console.print(
            "[green]Restored remote(s):[/green] "
            + ", ".join(f"{n} ({u})" for n, u in remotes.items())
        )

    # Restore upstream tracking for the current branch.
    # git-filter-repo strips this along with remotes, which causes
    # tools like VS Code to show "Publish Branch" instead of push/pull.
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if branch and "origin" in remotes:
        subprocess.run(
            ["git", "branch", "--set-upstream-to", f"origin/{branch}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )


# ---------------------------------------------------------------------------
# 6. rewrite_history
# ---------------------------------------------------------------------------
def rewrite_history(
    repo_path: str,
    messages: list[GeneratedMessage],
) -> dict[str, str]:
    """Rewrite git history using ``git filter-repo --commit-callback``.

    Uses ``--commit-callback`` with hash-based matching via
    ``commit.original_id``.  This avoids the duplicate-key problem that
    occurs with ``--message-callback`` when many commits share the same
    original message (e.g. dozens of "etc" commits).

    Steps performed:

    1. Verify that ``git-filter-repo`` is installed.
    2. Create a backup branch via :func:`create_backup`.
    3. Build a hash → new_message mapping.
    4. Write callback to a temp file (avoids Windows cmd length limits).
    5. Execute ``git filter-repo --force --commit-callback <file>``.
    6. Return a mapping of ``{short_hash: "before -> after"}`` entries.

    Parameters
    ----------
    repo_path:
        Path to the root of the git repository.
    messages:
        :class:`GeneratedMessage` instances to apply.

    Returns
    -------
    dict[str, str]
        Mapping of short hashes to human-readable before/after descriptions.

    Raises
    ------
    RuntimeError
        If ``git-filter-repo`` is not installed.
    subprocess.CalledProcessError
        If any git command fails.
    """
    # --- 1. Availability check ---
    if not check_filter_repo():
        instructions = get_install_instructions()
        raise RuntimeError(
            f"git-filter-repo is not installed.\n{instructions}"
        )

    # --- 2. Backup ---
    backup_branch = create_backup(repo_path)
    _console.print(
        f"[green]Backup branch created:[/green] {backup_branch}"
    )

    # --- 3. Build hash → new_message mapping ---
    hash_map: dict[str, str] = {}
    results: dict[str, str] = {}

    for msg in messages:
        # Compose new message
        if msg.body:
            new_message = f"{msg.subject}\n\n{msg.body}"
        else:
            new_message = msg.subject

        hash_map[msg.hash] = new_message
        results[msg.short_hash] = f"{msg.subject}"

    # --- 4. Save remotes (filter-repo strips them) ---
    remotes = save_remotes(repo_path)

    # --- 5. Build callback ---
    callback_script = _build_commit_callback(hash_map)

    # --- 6. Execute rewrite ---
    # Write callback to a temp file to avoid Windows command-line length
    # limits (8,191 chars for cmd.exe / ~32K for CreateProcess).  With
    # many commits the inline callback easily exceeds these limits.
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        prefix="gitre_callback_",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(callback_script)
        callback_file = tmp.name

    try:
        subprocess.run(
            [
                "git",
                "filter-repo",
                "--force",
                "--commit-callback",
                callback_file,
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        Path(callback_file).unlink(missing_ok=True)

    # --- 7. Restore remotes ---
    if remotes:
        restore_remotes(repo_path, remotes)

    _console.print("[green]History rewrite complete.[/green]")

    return results


# ---------------------------------------------------------------------------
# 6. write_changelog
# ---------------------------------------------------------------------------
def write_changelog(
    repo_path: str,
    changelog_content: str,
    file_path: str,
) -> None:
    """Write changelog content to a file inside the repository.

    Parameters
    ----------
    repo_path:
        Path to the root of the git repository.
    changelog_content:
        The rendered changelog text.
    file_path:
        Relative (to *repo_path*) or absolute path for the output file.
    """
    target = Path(file_path)
    if not target.is_absolute():
        target = Path(repo_path) / target

    # Ensure parent directories exist
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(changelog_content, encoding="utf-8")

    _console.print(f"[green]Changelog written to:[/green] {target}")


# ---------------------------------------------------------------------------
# 9. commit_artifacts
# ---------------------------------------------------------------------------
def commit_artifacts(repo_path: str, changelog_file: str | None = None) -> None:
    """Stage and commit gitre artifacts after a history rewrite.

    Commits ``.gitre/`` (analysis cache) and the changelog file if provided.
    No-op if nothing is staged.

    Parameters
    ----------
    repo_path:
        Path to the root of the git repository.
    changelog_file:
        Path to the changelog file that was written, or ``None``.
    """
    files_to_add: list[str] = [".gitre/"]
    if changelog_file:
        target = Path(changelog_file)
        if target.is_absolute():
            try:
                target = target.relative_to(Path(repo_path).resolve())
            except ValueError:
                pass
        files_to_add.append(str(target))

    subprocess.run(
        ["git", "add", "-f", *files_to_add],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )

    # Skip commit if nothing was staged
    status = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode == 0:
        return

    subprocess.run(
        ["git", "commit", "-m", "Add changelog and gitre analysis cache"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    _console.print("[green]Committed gitre artifacts.[/green]")


# ---------------------------------------------------------------------------
# 10. force_push
# ---------------------------------------------------------------------------
def force_push(repo_path: str) -> None:
    """Force-push the current branch to the first configured remote.

    Raises
    ------
    RuntimeError
        If no remotes are configured.
    subprocess.CalledProcessError
        If the push fails.
    """
    branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    branch = branch_result.stdout.strip()

    remote_result = subprocess.run(
        ["git", "remote"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    remotes = [r.strip() for r in remote_result.stdout.strip().splitlines() if r.strip()]
    if not remotes:
        raise RuntimeError("No remotes configured. Cannot push.")

    remote = remotes[0]
    _console.print(f"[yellow]Force-pushing {branch} to {remote}...[/yellow]")

    subprocess.run(
        ["git", "push", "--force", remote, branch],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    _console.print(f"[green]Force-pushed {branch} to {remote}.[/green]")


# ---------------------------------------------------------------------------
# 11. display_proposals
# ---------------------------------------------------------------------------
def display_proposals(
    messages: list[GeneratedMessage],
    commits: list[CommitInfo] | None = None,
) -> None:
    """Print proposed commit message rewrites to the console.

    Uses :pypi:`rich` tables for clear, formatted output.

    Parameters
    ----------
    messages:
        The generated replacement messages.
    commits:
        Optional list of :class:`CommitInfo` objects.  When supplied the
        original messages are pulled from these objects for a side-by-side
        comparison.  Otherwise only the proposed new messages are shown.
    """
    if not messages:
        _console.print("[yellow]No proposals to display.[/yellow]")
        return

    # Build a lookup for original messages if commits are provided
    originals: dict[str, str] = {}
    if commits:
        for c in commits:
            originals[c.hash] = c.original_message
            originals[c.short_hash] = c.original_message

    table = Table(
        title="Proposed Commit Message Rewrites",
        show_lines=True,
        expand=True,
    )
    table.add_column("Hash", style="cyan", no_wrap=True, width=10)
    if commits:
        table.add_column("Original", style="dim")
    table.add_column("Proposed Subject", style="green")
    table.add_column("Body", style="white")
    table.add_column("Category", style="magenta", no_wrap=True, width=12)
    table.add_column("Changelog", style="yellow")

    for msg in messages:
        row: list[str] = [msg.short_hash]
        if commits:
            original_text = originals.get(msg.hash, originals.get(msg.short_hash, "—"))
            row.append(original_text)
        row.extend([
            msg.subject,
            msg.body or "—",
            msg.changelog_category,
            msg.changelog_entry,
        ])
        table.add_row(*row)

    _console.print()
    _console.print(table)
    _console.print()

    # Summary panel
    categories: dict[str, int] = {}
    for msg in messages:
        categories[msg.changelog_category] = categories.get(msg.changelog_category, 0) + 1
    summary_parts = [f"[bold]{len(messages)}[/bold] commit(s) to rewrite"]
    for cat, count in sorted(categories.items()):
        summary_parts.append(f"  {cat}: {count}")
    _console.print(
        Panel("\n".join(summary_parts), title="Summary", border_style="blue")
    )


# ---------------------------------------------------------------------------
# 8. confirm_rewrite
# ---------------------------------------------------------------------------
def confirm_rewrite() -> bool:
    """Prompt the user for confirmation before rewriting history.

    Returns ``True`` if the user confirms, ``False`` otherwise.
    """
    return typer.confirm(
        "This will rewrite git history. Are you sure you want to proceed?",
        default=False,
    )
