"""
Sanitizer — privacy filter for indexed content.

Two layers:
  1. .mcpignore  — glob patterns to skip entire files/paths during indexing
  2. Regex redaction — replaces known sensitive patterns with [REDACTED]
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path


# ── Sensitive content patterns ─────────────────────────────────────────────────

# Each entry: (human-readable name, compiled pattern)
SENSITIVE_PATTERNS: list[tuple[str, re.Pattern]] = [
    # API keys — major providers
    ("Anthropic API key",       re.compile(r'sk-ant-[a-zA-Z0-9\-_]{20,}')),
    ("OpenAI API key",          re.compile(r'sk-[a-zA-Z0-9]{20,}')),
    ("GitHub token",            re.compile(r'gh[psuro]_[a-zA-Z0-9]{36,}')),
    ("AWS access key",          re.compile(r'AKIA[0-9A-Z]{16}')),
    ("AWS secret key",          re.compile(r'(?i)aws_secret[_\-]?access[_\-]?key\s*[=:]\s*["\']?[a-zA-Z0-9+/]{40}["\']?')),
    # Auth
    ("Bearer token",            re.compile(r'Bearer\s+[a-zA-Z0-9\-._~+/]{20,}')),
    ("Private key block",       re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----')),
    # Connection strings with embedded credentials
    ("Database URL",            re.compile(r'(?i)(postgres|postgresql|mysql|mongodb|redis)://[^:\s/]+:[^@\s]+@\S+')),
    # Generic secret assignments (key = "longvalue")
    ("Secret assignment",       re.compile(r'(?i)\b(password|secret|api_key|access_token|auth_token)\s*[=:]\s*["\']?[a-zA-Z0-9+/\-_]{20,}["\']?')),
]


def redact(content: str) -> tuple[str, list[str]]:
    """
    Replace sensitive patterns in content with [REDACTED].

    Returns:
        (redacted_content, list of pattern names that matched)
    """
    triggered: list[str] = []
    for name, pattern in SENSITIVE_PATTERNS:
        new_content, count = pattern.subn("[REDACTED]", content)
        if count > 0:
            triggered.append(name)
            content = new_content
    return content, triggered


# ── .mcpignore ─────────────────────────────────────────────────────────────────


def load_mcpignore(project_root: Path) -> list[str]:
    """
    Load ignore patterns from .mcpignore at project root.
    Syntax: one glob pattern per line. Lines starting with # are comments.
    """
    ignore_file = project_root / ".mcpignore"
    if not ignore_file.exists():
        return []
    patterns: list[str] = []
    for line in ignore_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def is_ignored(path: Path, project_root: Path, patterns: list[str]) -> bool:
    """
    Return True if path matches any pattern from .mcpignore.

    Pattern rules:
      - Patterns ending in /  match any file inside that directory subtree
      - Other patterns use fnmatch glob against the full relative path AND the basename
    """
    if not patterns:
        return False
    relative = str(path.relative_to(project_root)).replace("\\", "/")
    for pattern in patterns:
        if pattern.endswith("/"):
            # Directory prefix match
            if relative.startswith(pattern) or f"/{pattern}" in f"/{relative}":
                return True
        else:
            # Glob match against full relative path or just filename
            if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern):
                return True
    return False
