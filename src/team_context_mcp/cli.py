"""
CLI for Team Context MCP Server.

Commands:
  team-mcp init             Scan the repo and index everything into the DB
  team-mcp index-prs        Index git commit history as PR context
  team-mcp add-memory       Save a one-off memory string into the vector DB
  team-mcp search           Quick search from the terminal (dev tool)
  team-mcp status           Show what's indexed for the current project
  team-mcp projects         List all indexed projects in ~/.team-mcp/
  team-mcp delete-project   Delete all indexed data for a project
  team-mcp serve            Start the MCP server
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_dotenv():
    """Load .env from cwd or project root into os.environ (no extra deps)."""
    for candidate in [Path.cwd() / ".env", _project_root() / ".env"]:
        if candidate.exists():
            with open(candidate) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
            break

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


# ── projects ──────────────────────────────────────────────────────────────────


@cli.command()
def projects():
    """List all indexed projects in ~/.team-mcp/."""
    db_dir = Path(os.environ.get("TEAM_MCP_DB_DIR", str(Path.home() / ".team-mcp")))

    if not db_dir.exists():
        console.print("[yellow]No projects indexed yet.[/yellow]")
        return

    dbs = sorted(db_dir.glob("*.db"))
    if not dbs:
        console.print("[yellow]No projects indexed yet.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Project", style="green")
    table.add_column("Size", justify="right")
    table.add_column("Path", style="dim")

    for db_path in dbs:
        name = db_path.stem
        size_kb = db_path.stat().st_size // 1024
        table.add_row(name, f"{size_kb} KB", str(db_path))

    console.print(f"\n[bold cyan]Indexed projects[/bold cyan] in {db_dir}\n")
    console.print(table)


# ── delete-project ─────────────────────────────────────────────────────────────


@cli.command("delete-project")
@click.argument("project")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def delete_project(project: str, yes: bool):
    """Delete all indexed data for a project."""
    db_dir = Path(os.environ.get("TEAM_MCP_DB_DIR", str(Path.home() / ".team-mcp")))
    db_path = db_dir / f"{project}.db"

    if not db_path.exists():
        console.print(f"[red]Project '{project}' not found.[/red]")
        return

    if not yes:
        click.confirm(f"Delete all indexed data for '{project}'?", abort=True)

    db_path.unlink()
    console.print(f"[green]✓ Project '{project}' deleted.[/green]")


# ── debug helpers ─────────────────────────────────────────────────────────────


def _get_debug_db():
    from team_context_mcp.debug_memory import DebugMemoryDB

    db_dir = os.environ.get("TEAM_MCP_DB_DIR", str(Path.home() / ".team-mcp"))
    db_path = Path(db_dir) / "debug-memory.db"
    return DebugMemoryDB(db_path)


# ── debug-scrape ───────────────────────────────────────────────────────────────


@cli.command("debug-scrape")
@click.option(
    "--repo", "-r", multiple=True,
    help="Repo to scrape, e.g. tiangolo/fastapi. Repeatable.",
)
@click.option("--max-prs", default=50, show_default=True, help="Max PRs per repo.")
@click.option("--token", default="", help="GitHub token (o ponelo en GITHUB_TOKEN o en .env).")
def debug_scrape(repo: tuple, max_prs: int, token: str):
    """Scrape bug-fix PRs from GitHub repos into la debug memory DB."""
    # Cargamos .env antes de resolver el token para que GITHUB_TOKEN del archivo esté disponible
    _load_dotenv()
    from team_context_mcp.debug_memory.scraper import GitHubScraper

    # Prioridad: --token flag > GITHUB_TOKEN del entorno (puede venir del .env cargado arriba)
    resolved_token = token or os.environ.get("GITHUB_TOKEN", "")

    repos = list(repo) or [
        "tiangolo/fastapi",
        "pallets/flask",
        "python/cpython",
    ]

    scraper = GitHubScraper(token=resolved_token or None)

    if not scraper.token:
        console.print(
            "[yellow]Sin token de GitHub — API pública (60 req/hora).\n"
            "  Para más velocidad: autenticarse con [bold]gh auth login[/bold] "
            "o poner GITHUB_TOKEN en el entorno.[/yellow]"
        )
    else:
        source = "flag --token" if token else ("GITHUB_TOKEN" if os.environ.get("GITHUB_TOKEN") else "gh CLI")
        console.print(f"[dim]Token resuelto desde: {source}[/dim]")
    db = _get_debug_db()

    total_new = 0
    for r in repos:
        console.print(f"\n[bold cyan]Scraping[/bold cyan] [green]{r}[/green]...")
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
            t = p.add_task(f"Fetching up to {max_prs} bug PRs from {r}...", total=None)
            try:
                n = scraper.scrape_repo(db, r, max_prs=max_prs)
                total_new += n
                p.update(t, description=f"{r}: {n} new events stored.")
            except Exception as e:
                p.update(t, description=f"[red]{r}: error — {e}[/red]")

    db.close()
    console.print(f"\n[bold green]Done![/bold green] {total_new} new debug events indexed.")
    console.print("Run [bold]team-mcp debug-embed[/bold] to generate embeddings, then [bold]team-mcp debug-query[/bold] to test.")


# ── debug-embed ────────────────────────────────────────────────────────────────


@cli.command("debug-embed")
def debug_embed():
    """Generate embeddings for debug events that don't have one yet."""
    from team_context_mcp.embedder import Embedder

    db = _get_debug_db()
    pending = db.events_without_embeddings()

    if not pending:
        console.print("[green]All debug events already have embeddings.[/green]")
        db.close()
        return

    console.print(f"Generating embeddings for [bold]{len(pending)}[/bold] events...")
    texts = [
        f"{e['title']} {e['problem_desc'] or ''} {e['solution_desc'] or ''}".strip()
        for e in pending
    ]

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
        t = p.add_task("Embedding...", total=None)
        embeddings = Embedder.embed_batch(texts)
        p.update(t, description=f"Embedded {len(embeddings)} events.")

    for event, embedding in zip(pending, embeddings):
        db.update_embedding(event["id"], embedding)

    db.close()
    console.print(f"[bold green]Done![/bold green] {len(pending)} embeddings stored.")


# ── debug-query ────────────────────────────────────────────────────────────────


@cli.command("debug-query")
@click.argument("query")
@click.option("--top-k", default=5, show_default=True)
def debug_query(query: str, top_k: int):
    """Search the debug memory for bugs similar to QUERY."""
    from team_context_mcp.embedder import Embedder

    db = _get_debug_db()
    total = db.total_count()

    if total == 0:
        console.print("[yellow]Debug memory is empty. Run `team-mcp debug-scrape` first.[/yellow]")
        db.close()
        return

    embedding = Embedder.embed(query)
    results = db.search(embedding, top_k=top_k)
    db.close()

    if not results:
        console.print("[yellow]No similar bugs found.[/yellow]")
        return

    console.print(f"\n[bold]Query:[/bold] {query}\n")

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Score", width=6, justify="right")
    table.add_column("Repo", width=20)
    table.add_column("Title", width=40)
    table.add_column("Date", width=10)
    table.add_column("URL", width=50)

    for r in results:
        table.add_row(
            str(r["similarity_score"]),
            r["repo"],
            r["title"][:40],
            r["date"],
            r["url"],
        )

    console.print(table)

    # Show detail for top result
    if results:
        top = results[0]
        console.print(f"\n[bold cyan]Top match detail[/bold cyan]")
        if top["problem"]:
            console.print(f"[bold]Problem:[/bold] {top['problem'][:300]}")
        if top["solution"]:
            console.print(f"[bold]Solution:[/bold] {top['solution'][:300]}")


# ── debug-stats ────────────────────────────────────────────────────────────────


@cli.command("debug-stats")
def debug_stats():
    """Show stats for the debug memory DB."""
    db = _get_debug_db()
    total = db.total_count()

    if total == 0:
        console.print("[yellow]Debug memory is empty. Run `team-mcp debug-scrape` first.[/yellow]")
        db.close()
        return

    counts = db.count_by_repo()
    db.close()

    console.print(f"\n[bold cyan]Debug Memory Stats[/bold cyan]\n")
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Repo", style="green")
    table.add_column("Events", justify="right")

    for repo_name, count in sorted(counts.items(), key=lambda x: -x[1]):
        table.add_row(repo_name, str(count))

    table.add_row("[bold]Total[/bold]", f"[bold]{total}[/bold]")
    console.print(table)


# ── serve ─────────────────────────────────────────────────────────────────────


@cli.command()
def serve():
    """Start the MCP server (stdio transport for Claude/Cursor integration)."""
    from team_context_mcp.server import run

    import sys
    print("Starting Team Context MCP server...", file=sys.stderr)
    run()


# ── entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
