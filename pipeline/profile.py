"""Profile loader — reads resume, experience, Google Docs, and selected story files.

Input:
    profile_dir:        path to directory containing resume.md, experience.md,
                        stories_index.md, and stories/
    google_docs_links:  list of dicts with 'url' and 'description' keys

Output (profile dict):
    {
        resume:      str  — contents of resume.md
        experience:  str  — contents of experience.md
        google_docs: str  — concatenated content from all Google Docs
        stories:     str  — full text of stories selected as relevant to the job
    }

Story selection uses a two-pass approach:
    Pass 1 (cheap): Claude reads stories_index.md and picks 8-10 relevant files
    Pass 2 (full):  Selected story files are read in full and included in prompts

Google Docs are fetched as plain text via the export URL:
    https://docs.google.com/document/d/{doc_id}/export?format=txt
This works for documents shared with "anyone with the link".
"""
import logging
import os
import re
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

_GDOC_ID_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")

_STORY_SELECT_PROMPT = """You are helping select the most relevant career stories for a job application.

## Job Details
Company: {company}
Title: {title}
Key requirements from description:
{description_excerpt}

## Available Stories Index
Each entry includes a title, keywords, a full STAR summary, and a relevance hint.
Use the summary as your primary signal — it contains the situation, actions, results, and context.

{index}

## Instructions
Select the 8-10 most relevant story files for this specific job.
Return ONLY a JSON array of filenames, e.g.:
["reorg_2021.md", "team_growth.md", "ads_platform.md"]

Prioritize stories whose summaries demonstrate:
- Leadership scope and team size matching the role level
- Domain or technical area overlap with the job description
- Outcomes and impact that would resonate with this company
- Situations similar to challenges this role will face

Do not select based on keyword overlap alone — read the summaries."""


def _extract_doc_id(url: str) -> Optional[str]:
    m = _GDOC_ID_RE.search(url)
    return m.group(1) if m else None


class ProfileLoader:
    def __init__(
        self,
        profile_dir: str = "profile",
        google_docs_links: Optional[List[dict]] = None,
        provider=None,
    ):
        self.profile_dir = profile_dir
        self.google_docs_links = google_docs_links or []
        self.provider = provider
        self._stories_dir = os.path.join(profile_dir, "stories")
        self._index_path = os.path.join(profile_dir, "stories_index.md")

    def _read_file(self, filename: str) -> str:
        path = os.path.join(self.profile_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except FileNotFoundError:
            return ""

    def _fetch_google_doc(self, url: str, description: str) -> str:
        doc_id = _extract_doc_id(url)
        if not doc_id:
            logger.warning("Could not extract doc ID from URL: %s", url)
            return ""
        export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
        try:
            resp = requests.get(export_url, timeout=15)
            resp.raise_for_status()
            return f"[Source: {description}]\n{resp.text.strip()}"
        except Exception as exc:
            logger.warning("Failed to fetch Google Doc '%s': %s", description, exc)
            return ""

    def select_stories(self, job: dict) -> str:
        """Two-pass story selection: pick relevant files via index, then read in full.

        Input:  job dict with company, title, description
        Output: concatenated text of selected story files
        """
        if not os.path.exists(self._index_path):
            logger.info("No stories_index.md found — skipping story selection")
            return ""
        if not os.path.isdir(self._stories_dir):
            return ""

        index_text = open(self._index_path, encoding="utf-8").read()
        if not index_text.strip():
            return ""

        # Pass 1: ask provider (fast model) to select relevant files
        selected_files = []
        if self.provider:
            try:
                import json as _json
                prompt = _STORY_SELECT_PROMPT.format(
                    company=job.get("company", ""),
                    title=job.get("title", ""),
                    description_excerpt=job.get("description", "")[:1500],
                    index=index_text[:4000],
                )
                raw = self.provider.complete_fast(prompt, max_tokens=300)
                json_match = re.search(r"\[.*?\]", raw, re.DOTALL)
                if json_match:
                    selected_files = _json.loads(json_match.group())
            except Exception as exc:
                logger.warning("Story selection failed: %s", exc)

        # Fallback: use all stories if selection failed
        if not selected_files:
            selected_files = [
                f for f in os.listdir(self._stories_dir) if f.endswith(".md")
            ][:10]

        # Pass 2: read selected files in full
        parts = []
        for fname in selected_files:
            fpath = os.path.join(self._stories_dir, fname)
            if not os.path.exists(fpath):
                logger.warning("Selected story file not found: %s", fname)
                continue
            try:
                content = open(fpath, encoding="utf-8").read().strip()
                parts.append(f"[Story: {fname}]\n{content}")
            except Exception as exc:
                logger.warning("Could not read story %s: %s", fname, exc)

        logger.info("Loaded %d/%d selected story files", len(parts), len(selected_files))
        return "\n\n---\n\n".join(parts)

    def load(self, job: Optional[dict] = None) -> dict:
        """Load full profile. Pass job dict to enable story selection.

        Input:  job (optional) — if provided, selects relevant stories for this job
        Output: profile dict with resume, experience, google_docs, stories
        """
        resume = self._read_file("resume.md")
        experience = self._read_file("experience.md")

        doc_parts = []
        for link in self.google_docs_links:
            content = self._fetch_google_doc(
                link.get("url", ""), link.get("description", "Google Doc")
            )
            if content:
                doc_parts.append(content)

        stories = self.select_stories(job) if job else ""

        return {
            "resume": resume,
            "experience": experience,
            "google_docs": "\n\n".join(doc_parts),
            "stories": stories,
        }

    def full_text(self, profile: dict) -> str:
        """Concatenate all profile sources into a single text block for Claude prompts."""
        parts = []
        if profile.get("resume"):
            parts.append(f"=== RESUME ===\n{profile['resume']}")
        if profile.get("experience"):
            parts.append(f"=== ADDITIONAL EXPERIENCE & BACKGROUND ===\n{profile['experience']}")
        if profile.get("google_docs"):
            parts.append(f"=== SUPPLEMENTARY DOCUMENTS ===\n{profile['google_docs']}")
        if profile.get("stories"):
            parts.append(f"=== RELEVANT CAREER STORIES ===\n{profile['stories']}")
        return "\n\n".join(parts)
