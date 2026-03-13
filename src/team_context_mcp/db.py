"""
Vector DB backed by SQLite + sqlite-vec.
Each record has: id, project, type, content, source_path, date, priority, embedding.
"""

import sqlite3
import sqlite_vec
import json
import struct
import time
from pathlib import Path
from typing import Optional


def _encode(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _decode(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


class VectorDB:
    DIM = 384  # all-MiniLM-L6-v2 dimension

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS documents (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                project     TEXT NOT NULL,
                type        TEXT NOT NULL,
                content     TEXT NOT NULL,
                source_path TEXT,
                date        REAL DEFAULT (unixepoch()),
                priority    REAL DEFAULT 0.5
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS embeddings USING vec0(
                doc_id INTEGER PRIMARY KEY,
                embedding float[{self.DIM}]
            );
        """)
        self.conn.commit()

    def insert(
        self,
        project: str,
        doc_type: str,
        content: str,
        embedding: list[float],
        source_path: str = "",
        priority: float = 0.5,
        date: Optional[float] = None,
    ) -> int:
        ts = date if date is not None else time.time()
        cur = self.conn.execute(
            "INSERT INTO documents (project, type, content, source_path, date, priority) VALUES (?,?,?,?,?,?)",
            (project, doc_type, content, source_path, ts, priority),
        )
        doc_id = cur.lastrowid
        self.conn.execute(
            "INSERT INTO embeddings (doc_id, embedding) VALUES (?, ?)",
            (doc_id, _encode(embedding)),
        )
        self.conn.commit()
        return doc_id

    def search(
        self,
        query_embedding: list[float],
        project: str,
        top_k: int = 5,
        doc_type: Optional[str] = None,
    ) -> list[dict]:
        """
        Returns ranked results using:
          score = semantic_similarity + architectural_priority + recency
        All components normalized to [0, 1].
        """
        type_filter = "AND d.type = ?" if doc_type else ""
        params: list = [_encode(query_embedding), top_k * 10, project]
        if doc_type:
            params.append(doc_type)

        rows = self.conn.execute(
            f"""
            SELECT
                d.id,
                d.type,
                d.content,
                d.source_path,
                d.date,
                d.priority,
                e.distance
            FROM embeddings e
            JOIN documents d ON e.doc_id = d.id
            WHERE e.embedding MATCH ?
              AND k = ?
              AND d.project = ?
              {type_filter}
            ORDER BY e.distance ASC
            """,
            params,
        ).fetchall()

        if not rows:
            return []

        # Normalize distance to similarity [0,1]
        distances = [r[6] for r in rows]
        max_d = max(distances) if distances else 1.0
        min_d = min(distances) if distances else 0.0
        range_d = max_d - min_d if max_d != min_d else 1.0

        # Normalize recency (unix timestamp → [0,1])
        dates = [r[4] for r in rows]
        max_t = max(dates) if dates else time.time()
        min_t = min(dates) if dates else 0.0
        range_t = max_t - min_t if max_t != min_t else 1.0

        results = []
        for row in rows:
            doc_id, doc_type_, content, source_path, date, priority, distance = row
            semantic = 1.0 - (distance - min_d) / range_d
            recency = (date - min_t) / range_t
            score = (semantic * 0.6) + (priority * 0.25) + (recency * 0.15)
            results.append(
                {
                    "id": doc_id,
                    "type": doc_type_,
                    "content": content,
                    "source_path": source_path,
                    "score": round(score, 4),
                    "semantic_similarity": round(semantic, 4),
                }
            )

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def delete_project(self, project: str):
        doc_ids = [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM documents WHERE project = ?", (project,)
            ).fetchall()
        ]
        if doc_ids:
            placeholders = ",".join("?" * len(doc_ids))
            self.conn.execute(
                f"DELETE FROM embeddings WHERE doc_id IN ({placeholders})", doc_ids
            )
        self.conn.execute("DELETE FROM documents WHERE project = ?", (project,))
        self.conn.commit()

    def count(self, project: str) -> dict:
        rows = self.conn.execute(
            "SELECT type, COUNT(*) FROM documents WHERE project = ? GROUP BY type",
            (project,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def close(self):
        self.conn.close()
