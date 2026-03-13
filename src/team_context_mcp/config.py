"""
Config loader — reads mcp.config.json from the project root.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Optional


DEFAULT_CONFIG = {
    "priority_files": [
        "docs/architecture.md",
        "team/context.md",
        "src/domain/",
    ],
    "skills_dir": "skills",
    "team_dir": "team",
    "db_path": ".mcp/context.db",
    "top_k": 5,
    "similarity_threshold": 0.35,
}


def load_config(project_root: Path) -> dict:
    cfg_path = project_root / "mcp.config.json"
    config = dict(DEFAULT_CONFIG)
    if cfg_path.exists():
        with open(cfg_path) as f:
            overrides = json.load(f)
        config.update(overrides)
    return config


def save_default_config(project_root: Path):
    cfg_path = project_root / "mcp.config.json"
    if not cfg_path.exists():
        with open(cfg_path, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
