"""
GitHub scraper for debug memory.

Fetches closed/merged PRs with bug-related labels from GitHub repos
and stores them as debug_events in DebugMemoryDB.

Rate limits:
  - Authenticated:   5 000 req/hour (~1.4/sec)
  - Unauthenticated:    60 req/hour

Token resolution order (automatic, no config needed):
  1. token= argument passed explicitly
  2. GITHUB_TOKEN environment variable
  3. gh CLI  →  `gh auth token`  (if installed and authenticated)
  4. Unauthenticated (60 req/hour)

Usage:
    scraper = GitHubScraper()   # resolves token automatically
    n = scraper.scrape_repo(db, "tiangolo/fastapi", max_prs=50)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from .storage import DebugMemoryDB


def _resolve_github_token(explicit: Optional[str] = None) -> Optional[str]:
    """
    Resolve a GitHub token from multiple sources, in priority order:
    1. Explicit argument
    2. GITHUB_TOKEN env var
    3. gh CLI  (`gh auth token`)
    """
    if explicit:
        return explicit
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=5
        )
        token = result.stdout.strip()
        if token and result.returncode == 0:
            return token
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # gh not installed or timed out
    return None

# Labels that indicate a bug-fix PR (case-insensitive substring match)
BUG_LABEL_PATTERNS = ["bug", "fix", "hotfix", "defect", "regression", "patch"]

# Minimum body length to consider a PR description useful
MIN_BODY_LEN = 50


class RateLimitError(Exception):
    """Raised when GitHub API rate limit is hit."""


class GitHubScraper:
    BASE_URL = "https://api.github.com"

    def __init__(self, token: Optional[str] = None):
        self.token = _resolve_github_token(token)
        self._remaining = 60
        self._reset_at = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def scrape_repo(
        self,
        db: DebugMemoryDB,
        repo: str,
        max_prs: int = 50,
        since_ts: Optional[int] = None,
    ) -> int:
        """
        Fetch bug-fix PRs for `repo` and upsert them into `db`.

        Args:
            db:       DebugMemoryDB instance.
            repo:     "{owner}/{name}" e.g. "tiangolo/fastapi".
            max_prs:  Maximum number of PRs to store.
            since_ts: Only fetch PRs created after this Unix timestamp.

        Returns:
            Number of newly inserted events.
        """
        inserted = 0
        seen_ids: set[str] = set()

        for label_group in [["bug"], ["fix"], ["hotfix"]]:
            if inserted >= max_prs:
                break
            label_str = ",".join(label_group)
            prs = self._fetch_bug_prs(repo, label_str, max_prs - inserted, since_ts)
            for pr in prs:
                event_id = f"{repo}#{pr['number']}"
                if event_id in seen_ids:
                    continue
                seen_ids.add(event_id)

                event = self._extract_event(pr, repo)
                if event is None:
                    continue

                is_new = db.upsert(event)
                if is_new:
                    inserted += 1
                if inserted >= max_prs:
                    break

        return inserted

    # ── Internals ─────────────────────────────────────────────────────────────

    def _fetch_bug_prs(
        self,
        repo: str,
        labels: str,
        limit: int,
        since_ts: Optional[int],
    ) -> list[dict]:
        """
        Use /repos/{owner}/{repo}/issues?state=closed&labels=... to find PRs.
        Issues endpoint supports label filtering; PRs appear as issues with
        a 'pull_request' key.
        """
        results: list[dict] = []
        page = 1
        since_iso = _ts_to_iso(since_ts) if since_ts else None

        while len(results) < limit:
            params: dict = {
                "state": "closed",
                "labels": labels,
                "per_page": min(100, limit - len(results) + 20),
                "page": page,
                "sort": "updated",
                "direction": "desc",
            }
            if since_iso:
                params["since"] = since_iso

            url = f"{self.BASE_URL}/repos/{repo}/issues"
            data = self._get(url, params)

            if not data:
                break  # empty page → done

            for item in data:
                # Only PRs (issues have no 'pull_request' key)
                if "pull_request" not in item:
                    continue
                # Only merged (pull_request.merged_at is set)
                if not item["pull_request"].get("merged_at"):
                    continue
                if not _has_bug_label(item.get("labels", [])):
                    continue
                results.append(item)
                if len(results) >= limit:
                    break

            if len(data) < params["per_page"]:
                break  # last page
            page += 1

        return results

    def _extract_event(self, issue: dict, repo: str) -> Optional[dict]:
        """
        Transform a raw GitHub issue/PR dict into a debug_event dict.
        Returns None if the PR doesn't have enough useful content.
        """
        body = issue.get("body") or ""
        title = issue.get("title", "").strip()

        if not title:
            return None

        problem, solution = _parse_body(body)

        # Skip PRs with no description at all
        if len(body) < MIN_BODY_LEN and not problem and not solution:
            return None

        pr_number = issue["number"]
        pr_url = issue.get("html_url") or f"https://github.com/{repo}/pull/{pr_number}"

        created_at = _iso_to_ts(issue.get("created_at"))
        merged_at_str = issue.get("pull_request", {}).get("merged_at")
        merged_at = _iso_to_ts(merged_at_str) if merged_at_str else None

        labels = [lbl["name"] for lbl in issue.get("labels", [])]
        author = (issue.get("user") or {}).get("login", "")

        return {
            "id": f"{repo}#{pr_number}",
            "repo": repo,
            "source_type": "pull_request",
            "source_url": pr_url,
            "title": title,
            "problem_desc": problem or _truncate(body, 500),
            "solution_desc": solution,
            "files_changed": [],  # populated later if needed
            "labels": labels,
            "created_at": created_at,
            "merged_at": merged_at,
            "author": author,
            "embedding": None,  # generated separately
        }

    def _get(self, url: str, params: Optional[dict] = None) -> list[dict]:
        """HTTP GET with rate-limit handling and exponential backoff."""
        self._maybe_wait_for_rate_limit()

        if params:
            url = url + "?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")

        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    self._update_rate_limit(dict(resp.headers))
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 403 and "rate limit" in (e.reason or "").lower():
                    self._handle_rate_limit(e.headers)
                elif e.code == 404:
                    return []
                elif e.code in (429, 503) and attempt < 3:
                    time.sleep(2 ** attempt)
                else:
                    raise
            except urllib.error.URLError:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                else:
                    raise

        return []

    def _update_rate_limit(self, headers: dict):
        remaining = headers.get("X-RateLimit-Remaining") or headers.get(
            "x-ratelimit-remaining"
        )
        reset = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
        if remaining is not None:
            self._remaining = int(remaining)
        if reset is not None:
            self._reset_at = float(reset)

    def _handle_rate_limit(self, headers):
        self._update_rate_limit(dict(headers))
        wait = max(0, self._reset_at - time.time()) + 5
        time.sleep(wait)

    def _maybe_wait_for_rate_limit(self):
        if self._remaining <= 5 and self._reset_at > time.time():
            wait = self._reset_at - time.time() + 2
            time.sleep(wait)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _has_bug_label(labels: list[dict]) -> bool:
    for lbl in labels:
        name = lbl.get("name", "").lower()
        if any(pat in name for pat in BUG_LABEL_PATTERNS):
            return True
    return False


_PROBLEM_HEADERS = re.compile(
    r"(?:^|\n)#+\s*(?:problem|issue|bug|what.s wrong|background|context|motivation)[^\n]*\n",
    re.IGNORECASE,
)
_SOLUTION_HEADERS = re.compile(
    r"(?:^|\n)#+\s*(?:solution|fix|changes|how it.s fixed|approach|resolution)[^\n]*\n",
    re.IGNORECASE,
)
_SECTION_HEADER = re.compile(r"\n#+\s", re.MULTILINE)


def _parse_body(body: str) -> tuple[str, str]:
    """
    Attempt to extract problem and solution sections from a PR body.
    Falls back to (first_half, second_half) if no headers found.
    """
    if not body:
        return "", ""

    prob_match = _PROBLEM_HEADERS.search(body)
    sol_match = _SOLUTION_HEADERS.search(body)

    problem = ""
    solution = ""

    if prob_match:
        start = prob_match.end()
        # Find next section header
        next_sec = _SECTION_HEADER.search(body, start)
        end = next_sec.start() if next_sec else len(body)
        problem = _truncate(body[start:end].strip(), 600)

    if sol_match:
        start = sol_match.end()
        next_sec = _SECTION_HEADER.search(body, start)
        end = next_sec.start() if next_sec else len(body)
        solution = _truncate(body[start:end].strip(), 600)

    return problem, solution


def _truncate(text: str, max_len: int) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "…"


def _ts_to_iso(ts: int) -> str:
    import datetime
    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_to_ts(iso: Optional[str]) -> int:
    if not iso:
        return int(time.time())
    import datetime
    try:
        dt = datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
        return int(dt.timestamp())
    except ValueError:
        return int(time.time())
