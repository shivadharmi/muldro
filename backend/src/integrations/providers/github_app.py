"""GitHub App native adapter — issues, PRs, repos, code search.

Uses personal access token or GitHub App installation token.
Covers the most common GitHub operations with normalized return types.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


@dataclass(frozen=True, slots=True)
class GitHubIssue:
    number: int
    title: str
    state: str
    html_url: str
    body: str | None = None
    labels: list[str] | None = None
    assignees: list[str] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class GitHubPR:
    number: int
    title: str
    state: str
    html_url: str
    head_ref: str
    base_ref: str
    body: str | None = None
    draft: bool = False
    mergeable: bool | None = None
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class GitHubRepo:
    full_name: str
    description: str | None
    html_url: str
    default_branch: str
    language: str | None = None
    stars: int = 0
    open_issues: int = 0


@dataclass(frozen=True, slots=True)
class CodeSearchResult:
    path: str
    repository: str
    html_url: str
    fragment: str | None = None


class GitHubAppAdapter:
    """Direct GitHub API adapter using access token."""

    def __init__(self, access_token: str):
        self._token = access_token
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    # ── Issues ────────────────────────────────────────────────────────────

    async def list_issues(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        per_page: int = 20,
    ) -> list[GitHubIssue]:
        """List issues for a repository."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues",
                headers=self._headers,
                params={"state": state, "per_page": per_page},
                timeout=30.0,
            )
            resp.raise_for_status()
            return [_parse_issue(i) for i in resp.json() if "pull_request" not in i]

    async def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str | None = None,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ) -> GitHubIssue:
        """Create a new issue."""
        payload: dict = {"title": title}
        if body:
            payload["body"] = body
        if labels:
            payload["labels"] = labels
        if assignees:
            payload["assignees"] = assignees

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues",
                headers=self._headers,
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            return _parse_issue(resp.json())

    async def update_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
        labels: list[str] | None = None,
    ) -> GitHubIssue:
        """Update an existing issue."""
        payload: dict = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state
        if labels is not None:
            payload["labels"] = labels

        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_number}",
                headers=self._headers,
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            return _parse_issue(resp.json())

    async def add_comment(self, owner: str, repo: str, issue_number: int, body: str) -> dict:
        """Add a comment to an issue or PR."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_number}/comments",
                headers=self._headers,
                json={"body": body},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return {"comment_id": data["id"], "html_url": data["html_url"]}

    # ── Pull Requests ─────────────────────────────────────────────────────

    async def list_prs(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        per_page: int = 20,
    ) -> list[GitHubPR]:
        """List pull requests for a repository."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls",
                headers=self._headers,
                params={"state": state, "per_page": per_page},
                timeout=30.0,
            )
            resp.raise_for_status()
            return [_parse_pr(p) for p in resp.json()]

    async def create_pr(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str | None = None,
        draft: bool = False,
    ) -> GitHubPR:
        """Create a pull request."""
        payload: dict = {"title": title, "head": head, "base": base, "draft": draft}
        if body:
            payload["body"] = body

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls",
                headers=self._headers,
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            return _parse_pr(resp.json())

    async def merge_pr(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        merge_method: str = "squash",
        commit_title: str | None = None,
    ) -> dict:
        """Merge a pull request."""
        payload: dict = {"merge_method": merge_method}
        if commit_title:
            payload["commit_title"] = commit_title

        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/merge",
                headers=self._headers,
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Get the diff for a pull request."""
        headers = {**self._headers, "Accept": "application/vnd.github.diff"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}",
                headers=headers,
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.text

    # ── Code Search ───────────────────────────────────────────────────────

    async def search_code(self, query: str, per_page: int = 10) -> list[CodeSearchResult]:
        """Search code across repositories."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GITHUB_API_BASE}/search/code",
                headers=self._headers,
                params={"q": query, "per_page": per_page},
                timeout=30.0,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return [
                CodeSearchResult(
                    path=item.get("path", ""),
                    repository=item.get("repository", {}).get("full_name", ""),
                    html_url=item.get("html_url", ""),
                    fragment=item.get("text_matches", [{}])[0].get("fragment")
                    if item.get("text_matches")
                    else None,
                )
                for item in items
            ]

    async def search_repos(self, query: str, per_page: int = 10) -> list[GitHubRepo]:
        """Search repositories."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GITHUB_API_BASE}/search/repositories",
                headers=self._headers,
                params={"q": query, "per_page": per_page},
                timeout=30.0,
            )
            resp.raise_for_status()
            return [_parse_repo(r) for r in resp.json().get("items", [])]

    # ── Repository ────────────────────────────────────────────────────────

    async def get_repo(self, owner: str, repo: str) -> GitHubRepo:
        """Get repository info."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}",
                headers=self._headers,
                timeout=30.0,
            )
            resp.raise_for_status()
            return _parse_repo(resp.json())

    async def get_file_content(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> str:
        """Get the content of a file in a repository."""
        params = {}
        if ref:
            params["ref"] = ref

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}",
                headers={**self._headers, "Accept": "application/vnd.github.raw+json"},
                params=params,
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.text


def _parse_issue(data: dict) -> GitHubIssue:
    return GitHubIssue(
        number=data.get("number", 0),
        title=data.get("title", ""),
        state=data.get("state", ""),
        html_url=data.get("html_url", ""),
        body=data.get("body"),
        labels=[lbl.get("name", "") for lbl in data.get("labels", [])],
        assignees=[a.get("login", "") for a in data.get("assignees", [])],
        created_at=(
            datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
            if data.get("created_at")
            else None
        ),
        updated_at=(
            datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
            if data.get("updated_at")
            else None
        ),
    )


def _parse_pr(data: dict) -> GitHubPR:
    return GitHubPR(
        number=data.get("number", 0),
        title=data.get("title", ""),
        state=data.get("state", ""),
        html_url=data.get("html_url", ""),
        head_ref=data.get("head", {}).get("ref", ""),
        base_ref=data.get("base", {}).get("ref", ""),
        body=data.get("body"),
        draft=data.get("draft", False),
        mergeable=data.get("mergeable"),
        additions=data.get("additions", 0),
        deletions=data.get("deletions", 0),
        changed_files=data.get("changed_files", 0),
        created_at=(
            datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
            if data.get("created_at")
            else None
        ),
    )


def _parse_repo(data: dict) -> GitHubRepo:
    return GitHubRepo(
        full_name=data.get("full_name", ""),
        description=data.get("description"),
        html_url=data.get("html_url", ""),
        default_branch=data.get("default_branch", "main"),
        language=data.get("language"),
        stars=data.get("stargazers_count", 0),
        open_issues=data.get("open_issues_count", 0),
    )
