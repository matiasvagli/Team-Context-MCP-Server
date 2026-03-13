"""
CLI for Team Context MCP Server.

Commands:
  team-mcp init          Scan the repo and index everything into the DB
  team-mcp index-prs     Index git commit history as PR context
  team-mcp add-memory    Save a one-off memory string into the vector DB
  team-mcp search        Quick search from the terminal (dev tool)
  team-mcp status        Show what's indexed for the current project
  team-mcp serve         Start the MCP server
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


def _project_root() -> Path:
    """Walk up from cwd until we find a .git dir, or return cwd."""
    p = Path.cwd()
    for parent in [p, *p.parents]:
        if (parent / ".git").exists():
            return parent
    return p


def _detect_project(root: Path) -> str:
    try:
        import git

        repo = git.Repo(str(root))
        remote = repo.remotes.origin.url
        return remote.rstrip("/").split("/")[-1].replace(".git", "")
    except Exception:
        return root.name


def _get_db(project: str):
    from team_context_mcp.db import VectorDB

    db_dir = os.environ.get("TEAM_MCP_DB_DIR", str(Path.home() / ".team-mcp"))
    db_path = Path(db_dir) / f"{project}.db"
    return VectorDB(db_path)


# ── CLI group ─────────────────────────────────────────────────────────────────


@click.group()
def cli():
    """Team Context MCP — shared knowledge for LLM-assisted development."""
    pass


# ── init ──────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--project", "-p", default="", help="Project name (auto-detected from git)")
@click.option("--root", "-r", default="", help="Project root path (default: cwd)")
@click.option("--reset", is_flag=True, help="Delete existing index before re-indexing")
def init(project: str, root: str, reset: bool):
    """Scan the repo and index skills, team memory, and docs."""
    from team_context_mcp.config import load_config, save_default_config
    from team_context_mcp.indexer import index_skills, index_team_memory, index_docs

    project_root = Path(root) if root else _project_root()
    if not project:
        project = _detect_project(project_root)

    console.print(f"\n[bold cyan]Team Context MCP[/bold cyan] — init")
    console.print(f"  Project : [green]{project}[/green]")
    console.print(f"  Root    : {project_root}")

    save_default_config(project_root)
    config = load_config(project_root)
    db = _get_db(project)

    if reset:
        console.print("[yellow]  Resetting existing index...[/yellow]")
        db.delete_project(project)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Indexing skills...", total=None)
        n_skills = index_skills(db, project, project_root, config)
        progress.update(task, description=f"Skills indexed: {n_skills}")

        task2 = progress.add_task("Indexing team memory...", total=None)
        n_mem = index_team_memory(db, project, project_root, config)
        progress.update(task2, description=f"Team memory indexed: {n_mem}")

        task3 = progress.add_task("Indexing docs...", total=None)
        n_docs = index_docs(db, project, project_root, config)
        progress.update(task3, description=f"Docs indexed: {n_docs}")

    db.close()

    total = n_skills + n_mem + n_docs
    console.print(f"\n[bold green]Done![/bold green] {total} documents indexed for '{project}'.")
    console.print(
        "\nRun [bold]team-mcp serve[/bold] to start the MCP server, "
        "or add it to your MCP client config."
    )


# ── index-prs  ────────────────────────────────────────────────────────────────


@cli.command("index-prs")
@click.option("--project", "-p", default="", help="Project name (auto-detected)")
@click.option("--root", "-r", default="", help="Project root path")
@click.option("--limit", default=50, show_default=True, help="Max commits to index")
def index_prs(project: str, root: str, limit: int):
    """Index git commit history as PR/change context."""
    from team_context_mcp.indexer import index_prs_from_git

    project_root = Path(root) if root else _project_root()
    if not project:
        project = _detect_project(project_root)

    console.print(f"\n[bold cyan]Indexing PRs[/bold cyan] for [green]{project}[/green]")

    db = _get_db(project)
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
        t = p.add_task(f"Reading up to {limit} commits...", total=None)
        n = index_prs_from_git(db, project, project_root, limit=limit)
        p.update(t, description=f"Indexed {n} commits.")
    db.close()

    console.print(f"[green]Done![/green] {n} commits indexed.")


# ── add-memory ────────────────────────────────────────────────────────────────


@cli.command("add-memory")
@click.argument("text")
@click.option("--project", "-p", default="", help="Project name (auto-detected from git)")
def add_memory(text: str, project: str):
    """Save a one-off memory string into the vector DB."""
    import time
    from team_context_mcp.embedder import Embedder

    project_root = _project_root()
    if not project:
        project = _detect_project(project_root)

    db = _get_db(project)
    embedding = Embedder.embed(text)
    db.insert(
        project,
        doc_type="memory",
        content=text,
        embedding=embedding,
        source_path="session",
        priority=0.85,
        date=time.time(),
    )
    db.close()

    preview = text[:60]
    console.print(f'[green]✓ Memory saved:[/green] "{preview}"')


# ── search ────────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("query")
@click.option("--project", "-p", default="", help="Project name (auto-detected)")
@click.option("--top-k", default=5, show_default=True)
@click.option("--type", "doc_type", default="", help="Filter by type: skill|memory|pr|doc")
def search(query: str, project: str, top_k: int, doc_type: str):
    """Search the index from the terminal."""
    from team_context_mcp.embedder import Embedder

    project_root = _project_root()
    if not project:
        project = _detect_project(project_root)

    console.print(f"\n[bold]Query:[/bold] {query}")
    console.print(f"[bold]Project:[/bold] {project}\n")

    db = _get_db(project)
    embedding = Embedder.embed(query)
    results = db.search(embedding, project=project, top_k=top_k, doc_type=doc_type or None)
    db.close()

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Type", width=8)
    table.add_column("Score", width=6)
    table.add_column("Source", width=30)
    table.add_column("Preview", width=60)

    for r in results:
        preview = r["content"].replace("\n", " ")[:80] + "…"
        table.add_row(
            r["type"],
            str(r["score"]),
            r["source_path"] or "-",
            preview,
        )

    console.print(table)


# ── status ────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--project", "-p", default="", help="Project name (auto-detected)")
def status(project: str):
    """Show what's indexed for the current project."""
    project_root = _project_root()
    if not project:
        project = _detect_project(project_root)

    db = _get_db(project)
    counts = db.count(project)
    db.close()

    console.print(f"\n[bold cyan]Indexed entries[/bold cyan] for [green]{project}[/green]\n")

    if not counts:
        console.print("[yellow]Nothing indexed yet. Run `team-mcp init`.[/yellow]")
        return

    table = Table(show_header=False)
    table.add_column("Type", style="bold")
    table.add_column("Count", justify="right")
    total = 0
    for doc_type, count in sorted(counts.items()):
        table.add_row(doc_type, str(count))
        total += count
    table.add_row("[bold]Total[/bold]", f"[bold]{total}[/bold]")
    console.print(table)


# ── serve ─────────────────────────────────────────────────────────────────────


@cli.command()
def serve():
    """Start the MCP server (stdio transport for Claude/Cursor integration)."""
    from team_context_mcp.server import run

    console.print("[bold cyan]Starting Team Context MCP server...[/bold cyan]")
    run()


# ── entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
