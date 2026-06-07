"""GitHub API client for creating issues from user feedback."""
import os
import requests
from typing import Optional, List


class GitHubClient:
    """Wrapper around GitHub REST API for issue creation."""

    def __init__(self, token: Optional[str] = None, repo_owner: str = "saurabh1011",
                 repo_name: str = "job-pipeline"):
        """Initialize GitHub client with token and repo details.

        Args:
            token: GitHub personal access token (defaults to GITHUB_TOKEN env var)
            repo_owner: Repository owner username
            repo_name: Repository name
        """
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.api_base = "https://api.github.com"
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "job-pipeline-feedback",
        }
        if self.token:
            self.headers["Authorization"] = f"token {self.token}"

    def create_issue(self, title: str, body: str, labels: List[str] = None,
                    assignee: Optional[str] = None) -> dict:
        """Create a GitHub issue.

        Args:
            title: Issue title
            body: Issue description (markdown)
            labels: List of label names to attach
            assignee: Optional GitHub username to assign to

        Returns:
            Dict with keys: issue_number, issue_url, html_url

        Raises:
            RuntimeError: If GitHub API call fails
        """
        if not self.token:
            raise RuntimeError("GitHub token not configured (GITHUB_TOKEN env var)")

        url = f"{self.api_base}/repos/{self.repo_owner}/{self.repo_name}/issues"
        payload = {
            "title": title,
            "body": body,
            "labels": labels or [],
        }
        if assignee:
            payload["assignee"] = assignee

        try:
            response = requests.post(url, json=payload, headers=self.headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            return {
                "issue_number": data.get("number"),
                "issue_url": data.get("html_url"),
                "api_url": data.get("url"),
            }
        except requests.exceptions.Timeout:
            raise RuntimeError("GitHub API timeout after 10 seconds")
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                raise RuntimeError("GitHub authentication failed - invalid token")
            elif response.status_code == 403:
                raise RuntimeError("GitHub API rate limited or insufficient permissions")
            elif response.status_code == 404:
                raise RuntimeError(f"Repository not found: {self.repo_owner}/{self.repo_name}")
            else:
                raise RuntimeError(f"GitHub API error ({response.status_code}): {response.text}")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"GitHub API connection error: {str(e)}")

    def validate_token(self) -> bool:
        """Validate that the GitHub token is working.

        Returns:
            True if token is valid, False otherwise
        """
        if not self.token:
            return False

        try:
            url = f"{self.api_base}/user"
            response = requests.get(url, headers=self.headers, timeout=5)
            return response.status_code == 200
        except Exception:
            return False
