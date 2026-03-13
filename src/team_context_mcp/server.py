"""
MCP Server — Team Context

Exposes tools to LLM clients (Claude, Cursor, etc.) via the MCP protocol.

Tools:
  - get_context(prompt, project)   → returns ranked context for the prompt
  - list_skills(project)           → lists all indexed skills
  - add_memory(content, project)   → adds a team memory entry on the fly
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .db import VectorDB
from .embedder import Embedder
from .config import load_config

# ── Server init ──────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="team-context",
    instructions=(
        "Provides shared team context (skills, architecture decisions, PR history) "
        "relevant to the current task. Always call get_context first when working on "
        "a new task in a known project."
    ),
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_db(project: str) -> VectorDB:
    """Resolve DB path for a given project."""
    # Allow override via env var for multi-project setups
    db_dir = os.environ.get("TEAM_MCP_DB_DIR", str(Path.home() / ".team-mcp"))
    db_path = Path(db_dir) / f"{project}.db"
    return VectorDB(db_path)


def _detect_project() -> str:
    """Try to detect project name from git remote or cwd."""
    try:
        import git

        repo = git.Repo(search_parent_directories=True)
        remote = repo.remotes.origin.url
        # e.g. git@github.com:org/my-repo.git → my-repo
        name = remote.rstrip("/").split("/")[-1].replace(".git", "")
        return name
    except Exception:
        return Path.cwd().name


def _project_root() -> Path:
    """Return the git repo root, or cwd as fallback."""
    try:
        import git

        repo = git.Repo(search_parent_directories=True)
        return Path(repo.working_dir)
    except Exception:
        return Path.cwd()


# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool()
def get_context(prompt: str, project: str = "") -> str:
    """
    Returns the most relevant team context for a given prompt.
    Searches skills, team memory, and PR history, ranked by relevance.

    Args:
        prompt:  The developer's current task or question.
        project: Project name (auto-detected from git remote if omitted).
    """
    if not project:
        project = _detect_project()

    config = load_config(_project_root())
    threshold = config.get("similarity_threshold", 0.35)

    db = _get_db(project)
    embedding = Embedder.embed(prompt)
    results = db.search(embedding, project=project, top_k=5)
    db.close()

    results = [r for r in results if r["score"] >= threshold]

    if not results:
        return f"No relevant context found for project '{project}' (score below threshold {threshold})."

    lines = [f"# Team context for: {project}\n"]
    for r in results:
        label = f"[{r['type']}]".ljust(10)
        source = f"  ({r['source_path']})" if r["source_path"] else ""
        lines.append(f"{label} relevance: {r['semantic_similarity']:.2f}{source}")
        lines.append("")
        # Truncate long content to avoid blowing context window
        content = r["content"]
        if len(content) > 1500:
            content = content[:1500] + "\n... [truncated]"
        lines.append(content)
        lines.append("\n---")

    return "\n".join(lines)


@mcp.tool()
def list_skills(project: str = "") -> str:
    """
    Lists all indexed skills and memory entries for the project.

    Args:
        project: Project name (auto-detected if omitted).
    """
    if not project:
        project = _detect_project()

    db = _get_db(project)
    counts = db.count(project)
    db.close()

    if not counts:
        return f"No entries indexed for project '{project}'. Run `team-mcp init` first."

    lines = [f"Indexed entries for project '{project}':"]
    total = 0
    for doc_type, count in sorted(counts.items()):
        lines.append(f"  {doc_type:<12} {count} entries")
        total += count
    lines.append(f"\n  Total: {total}")
    return "\n".join(lines)


@mcp.tool()
def add_memory(content: str, project: str = "", priority: float = 0.7) -> str:
    """
    Adds a new team memory entry (architectural decision, lesson learned, etc.)
    directly from the LLM session — no file editing needed.

    Args:
        content:  The text to store (decision, convention, lesson).
        project:  Project name (auto-detected if omitted).
        priority: Importance weight 0–1 (default 0.7).
    """
    if not content.strip():
        return "Content cannot be empty."
    if not 0.0 <= priority <= 1.0:
        return "Priority must be between 0.0 and 1.0."

    if not project:
        project = _detect_project()

    embedding = Embedder.embed(content)
    db = _get_db(project)
    doc_id = db.insert(
        project=project,
        doc_type="memory",
        content=content,
        embedding=embedding,
        source_path="inline",
        priority=priority,
    )
    db.close()

    return f"Memory stored (id={doc_id}) for project '{project}'."


# ── Entry point ───────────────────────────────────────────────────────────────


def run():
    mcp.run()


if __name__ == "__main__":
    run()
