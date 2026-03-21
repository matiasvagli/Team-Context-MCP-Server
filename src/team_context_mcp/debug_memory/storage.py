"""
DebugMemoryDB — cross-project bug fix history.

Schema: debug_events table + sqlite-vec virtual table for embeddings.
DB lives at ~/.team-mcp/debug-memory.db (shared across all projects).
"""

from __future__ import annotations

import json
import sqlite3
import struct
import time
from pathlib import Path
from typing import Optional

import sqlite_vec


def _encode(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


class DebugMemoryDB:
    DIM = 384  # all-MiniLM-L6-v2

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS debug_events (
                id           TEXT PRIMARY KEY,
                repo         TEXT NOT NULL,
                source_type  TEXT NOT NULL DEFAULT 'pull_request',
                source_url   TEXT NOT NULL,
                title        TEXT NOT NULL,
                problem_desc TEXT,
                solution_desc TEXT,
                files_changed TEXT,
                labels       TEXT,
                created_at   INTEGER NOT NULL,
                merged_at    INTEGER,
                author       TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_debug_repo ON debug_events(repo);
            CREATE INDEX IF NOT EXISTS idx_debug_created ON debug_events(created_at);

            CREATE VIRTUAL TABLE IF NOT EXISTS debug_embeddings USING vec0(
                event_id TEXT PRIMARY KEY,
                embedding float[{self.DIM}]
            );
        """)
        self.conn.commit()

    def upsert(self, event: dict) -> bool:
        """Insert or replace a debug event. Returns True if newly inserted."""
        existing = self.conn.execute(
            "SELECT id FROM debug_events WHERE id = ?", (event["id"],)
        ).fetchone()

        self.conn.execute(
            """
            INSERT OR REPLACE INTO debug_events
                (id, repo, source_type, source_url, title, problem_desc,
                 solution_desc, files_changed, labels, created_at, merged_at, author)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event["id"],
                event["repo"],
                event.get("source_type", "pull_request"),
                event["source_url"],
                event["title"],
                event.get("problem_desc"),
                event.get("solution_desc"),
                json.dumps(event.get("files_changed", [])),
                json.dumps(event.get("labels", [])),
                event["created_at"],
                event.get("merged_at"),
                event.get("author"),
            ),
        )

        if event.get("embedding"):
            self.conn.execute(
                "INSERT OR REPLACE INTO debug_embeddings (event_id, embedding) VALUES (?, ?)",
                (event["id"], _encode(event["embedding"])),
            )

        self.conn.commit()
        return existing is None

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        repo: Optional[str] = None,
    ) -> list[dict]:
        """Return top-k similar debug events by vector similarity."""
        repo_filter = "AND e.repo = ?" if repo else ""
        params: list = [_encode(query_embedding), top_k * 3]
        if repo:
            params.append(repo)

        rows = self.conn.execute(
            f"""
            SELECT
                e.id, e.repo, e.source_url, e.title,
                e.problem_desc, e.solution_desc, e.labels,
                e.created_at, e.author,
                de.distance
            FROM debug_embeddings de
            JOIN debug_events e ON de.event_id = e.id
            WHERE de.embedding MATCH ?
              AND k = ?
              {repo_filter}
            ORDER BY de.distance ASC
            """,
            params,
        ).fetchall()

        if not rows:
            return []

        distances = [r["distance"] for r in rows]
        max_d = max(distances) if distances else 1.0
        min_d = min(distances) if distances else 0.0
        range_d = (max_d - min_d) if max_d != min_d else 1.0

        results = []
        for row in rows:
            similarity = 1.0 - (row["distance"] - min_d) / range_d
            results.append(
                {
                    "id": row["id"],
                    "repo": row["repo"],
                    "title": row["title"],
                    "problem": row["problem_desc"] or "",
                    "solution": row["solution_desc"] or "",
                    "url": row["source_url"],
                    "labels": json.loads(row["labels"] or "[]"),
                    "date": _ts_to_date(row["created_at"]),
                    "author": row["author"] or "",
                    "similarity_score": round(similarity, 4),
                }
            )

        results.sort(key=lambda x: x["similarity_score"], reverse=True)
        return results[:top_k]

    def count_by_repo(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT repo, COUNT(*) as cnt FROM debug_events GROUP BY repo"
        ).fetchall()
        return {r["repo"]: r["cnt"] for r in rows}

    def total_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM debug_events").fetchone()[0]

    def get_repo_date_range(self, repo: str) -> tuple[Optional[int], Optional[int]]:
        """Return (min_created_at, max_created_at) for a repo."""
        row = self.conn.execute(
            "SELECT MIN(created_at), MAX(created_at) FROM debug_events WHERE repo = ?",
            (repo,),
        ).fetchone()
        return (row[0], row[1])

    def has_embeddings(self) -> bool:
        n = self.conn.execute("SELECT COUNT(*) FROM debug_embeddings").fetchone()[0]
        return n > 0

    def events_without_embeddings(self) -> list[dict]:
        """Return events that have no embedding yet."""
        rows = self.conn.execute(
            """
            SELECT e.id, e.title, e.problem_desc, e.solution_desc
            FROM debug_events e
            LEFT JOIN debug_embeddings de ON e.id = de.event_id
            WHERE de.event_id IS NULL
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def update_embedding(self, event_id: str, embedding: list[float]):
        self.conn.execute(
            "INSERT OR REPLACE INTO debug_embeddings (event_id, embedding) VALUES (?, ?)",
            (event_id, _encode(embedding)),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()


def _ts_to_date(ts: Optional[int]) -> str:
    if not ts:
        return ""
    import datetime
    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
