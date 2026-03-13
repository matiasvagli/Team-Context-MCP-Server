"""
Indexer: scans files and inserts them into the VectorDB.
"""

from __future__ import annotations
import time
from pathlib import Path
from typing import Optional

from .db import VectorDB
from .embedder import Embedder
from .config import load_config


SKILL_EXTENSIONS = {".md", ".txt"}
PRIORITY_BOOST = {
    "skill": 0.9,
    "memory": 0.85,
    "pr": 0.7,
    "doc": 0.6,
}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Extract YAML-like frontmatter from markdown. Returns (metadata, body)."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    fm_str = content[3:end].strip()
    body = content[end + 4:].strip()
    meta: dict = {}
    for line in fm_str.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip().lower()] = val.strip().lower()
    return meta, body


def _priority_for_path(path: Path, priority_files: list[str], project_root: Path) -> float:
    relative = str(path.relative_to(project_root))
    for pf in priority_files:
        if relative.startswith(pf) or relative == pf:
            return 0.95
    return 0.5


def index_skills(db: VectorDB, project: str, project_root: Path, config: dict):
    skills_dir = project_root / config["skills_dir"]
    if not skills_dir.exists():
        return 0

    files = [f for f in skills_dir.rglob("*") if f.suffix in SKILL_EXTENSIONS and f.is_file()]
    if not files:
        return 0

    raw_contents = [_read(f) for f in files]
    parsed = [_parse_frontmatter(c) for c in raw_contents]
    entries = [(f, meta, body) for f, (meta, body) in zip(files, parsed) if body]

    embeddings = Embedder.embed_batch([body for _, _, body in entries])

    priority_files = config.get("priority_files", [])
    for (filepath, meta, body), embedding in zip(entries, embeddings):
        source_path = str(filepath.relative_to(project_root))
        prio = _priority_for_path(filepath, priority_files, project_root)
        deprecated = meta.get("status") == "deprecated"
        db.delete_by_source(project, source_path)
        db.insert(
            project=project,
            doc_type="skill",
            content=body,
            embedding=embedding,
            source_path=source_path,
            priority=prio,
            deprecated=deprecated,
        )

    return len(entries)


def index_team_memory(db: VectorDB, project: str, project_root: Path, config: dict):
    team_dir = project_root / config["team_dir"]
    if not team_dir.exists():
        return 0

    files = [f for f in team_dir.rglob("*") if f.suffix in SKILL_EXTENSIONS and f.is_file()]
    if not files:
        return 0

    raw_contents = [_read(f) for f in files]
    parsed = [_parse_frontmatter(c) for c in raw_contents]
    entries = [(f, meta, body) for f, (meta, body) in zip(files, parsed) if body]

    embeddings = Embedder.embed_batch([body for _, _, body in entries])

    priority_files = config.get("priority_files", [])
    for (filepath, meta, body), embedding in zip(entries, embeddings):
        source_path = str(filepath.relative_to(project_root))
        prio = _priority_for_path(filepath, priority_files, project_root)
        deprecated = meta.get("status") == "deprecated"
        db.delete_by_source(project, source_path)
        db.insert(
            project=project,
            doc_type="memory",
            content=body,
            embedding=embedding,
            source_path=source_path,
            priority=prio,
            deprecated=deprecated,
        )

    return len(entries)


def index_docs(db: VectorDB, project: str, project_root: Path, config: dict):
    docs_dir = project_root / "docs"
    readme = project_root / "README.md"
    indexed = 0

    targets: list[Path] = []
    if readme.exists():
        targets.append(readme)
    if docs_dir.exists():
        targets.extend(f for f in docs_dir.rglob("*") if f.suffix in SKILL_EXTENSIONS and f.is_file())

    if not targets:
        return 0

    raw_contents = [_read(f) for f in targets]
    parsed = [_parse_frontmatter(c) for c in raw_contents]
    entries = [(f, meta, body) for f, (meta, body) in zip(targets, parsed) if body]

    embeddings = Embedder.embed_batch([body for _, _, body in entries])
    priority_files = config.get("priority_files", [])

    for (filepath, meta, body), embedding in zip(entries, embeddings):
        source_path = str(filepath.relative_to(project_root))
        prio = _priority_for_path(filepath, priority_files, project_root)
        deprecated = meta.get("status") == "deprecated"
        db.delete_by_source(project, source_path)
        db.insert(
            project=project,
            doc_type="doc",
            content=body,
            embedding=embedding,
            source_path=source_path,
            priority=prio,
            deprecated=deprecated,
        )

    return len(entries)


def index_prs_from_git(db: VectorDB, project: str, project_root: Path, limit: int = 50):
    """
    Extracts merged PR-like info from git log (merge commits).
    For real PR body/comments you'd use the GitHub API — this is the offline fallback.
    """
    try:
        import git

        repo = git.Repo(str(project_root))
    except Exception:
        return 0

    indexed = 0
    for commit in repo.iter_commits(max_count=limit):
        message = commit.message.strip()
        if not message or len(message) < 10:
            continue

        # Include changed file list in content for better context
        try:
            changed = [item.a_path for item in commit.diff(commit.parents[0])] if commit.parents else []
        except Exception:
            changed = []

        files_str = "\n".join(f"  - {p}" for p in changed[:20])
        content = f"Commit: {message}\nFiles changed:\n{files_str}" if files_str else f"Commit: {message}"

        source_path = f"git:{commit.hexsha[:8]}"
        embedding = Embedder.embed(content)
        db.delete_by_source(project, source_path)
        db.insert(
            project=project,
            doc_type="pr",
            content=content,
            embedding=embedding,
            source_path=source_path,
            priority=0.7,
            date=float(commit.committed_date),
        )
        indexed += 1

    return indexed
