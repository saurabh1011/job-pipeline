"""Story ingestion pipeline.

Converts .docx / .txt / .md files from inbox/ into reviewed .md files with
Claude-generated YAML frontmatter, then finalizes them into stories/.

Flow:
    inbox/  →  process_inbox()  →  review/   (Claude adds header, you edit)
    review/ →  finalize()       →  stories/  (overwrite) + stories_index.md

Input formats supported: .docx, .txt, .md
Output format: .md with YAML frontmatter

Header schema (YAML frontmatter):
    title:          str   — one-line summary
    type:           str   — story | experience | metrics | leadership | project | other
    keywords:       list  — 5-8 tags for relevance matching
    summary:        str   — 4-6 sentence STAR summary with metrics and context
    relevance_hint: str   — when to use this file (guidance for selection pass)
"""
import hashlib
import json
import logging
import os
import re
import shutil
from typing import Dict, List, Optional

from pipeline.llm import LLMProvider

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".docx", ".txt", ".md", ".pdf"}

_REDACT_PROMPT = """You are a privacy editor. Redact the names of all people mentioned in the following career story, replacing each with a concise role-based placeholder in brackets.

Rules:
- Replace every person's name (first, last, or full) with their role, e.g. [DIRECT REPORT], [MANAGER], [VP OF PRODUCT], [PEER MANAGER], [ENGINEER], [RECRUITER], [CLIENT], [STAKEHOLDER], [SKIP MANAGER], [HR PARTNER]
- If you can't infer the role, use [COLLEAGUE]
- Do NOT redact the author — first-person references ("I", "my", "me") stay as-is
- Do NOT redact company names, product names, team names, or place names
- Keep every other word exactly as written — do not paraphrase or summarize
- If no names are present, return the text unchanged

Document:
---
{text}
---

Return ONLY the redacted text. No explanations, no preamble."""

_HEADER_PROMPT = """You are helping organize a senior Engineering Manager's career stories and experience documents for job applications.

Read the entire document below and generate a YAML frontmatter header. This header is the ONLY thing a job-matching system will read when deciding whether to include this story in a cover letter or resume — the full document will not be re-read at selection time. The summary must therefore capture everything decision-relevant about this story.

Document content:
---
{content}
---

Generate ONLY the YAML frontmatter block (between --- markers) with these exact fields:

- title: A single specific sentence. Include what you did, the scale, and the outcome.
  Bad:  "Led a reorg"
  Good: "Reorganized a 40-person ads eng org post-acquisition, reducing incidents 60% and growing revenue 35% YoY"

- type: One of: story | experience | metrics | leadership | project | other

- keywords: A list of 5-10 tags. Cover: domain (ads, infra, platform, consumer), actions (reorg, hiring, migration, launch), scale (team-size, company-stage), time period, and any technologies or business areas mentioned.
  Example: [reorg, ads-platform, managing-managers, 40-engineers, post-acquisition, revenue-growth, 2021]

- summary: 4-6 sentences following the STAR format. This is the most important field.
  Sentence 1: Situation — what was the context, challenge, or opportunity? Include company stage, team size, timeline.
  Sentence 2-3: Actions — what specifically did YOU do? Be concrete about decisions made, people managed, systems built.
  Sentence 4-5: Results — what changed? Use numbers wherever possible (%, headcount, latency, revenue, time saved).
  Sentence 6 (optional): Why it matters for future roles — what does this story demonstrate about your leadership?
  Write in first person, past tense. Do not pad or hedge.

- relevance_hint: One sentence on the job-posting signals that should trigger selecting this story.
  Example: "Select when JD mentions post-M&A integration, org design, or managing eng managers at scale"

Return ONLY the YAML block starting and ending with ---. No other text."""

_FALLBACK_HEADER = """---
title: Untitled — please add a title
type: other
keywords: [review-needed]
summary: Please review and add a summary describing the situation, your actions, results with metrics, and relevance to future roles.
relevance_hint: Please review and update this header
---"""


def _file_checksum(path: str) -> str:
    """MD5 checksum of file contents for change detection."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def extract_text_from_file(path: str) -> str:
    """Extract plain text from a supported file.

    Input:  file path (.docx, .txt, .md)
    Output: plain text string, stripped of leading/trailing whitespace
    Raises: ValueError for unsupported extensions
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext}. Supported: {', '.join(SUPPORTED_EXTENSIONS)}")

    if ext == ".docx":
        from docx import Document
        doc = Document(path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs).strip()

    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(p.strip() for p in pages if p.strip()).strip()

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().strip()


def generate_header(content: str, provider: LLMProvider) -> str:
    """Generate a YAML frontmatter header for a story file.

    Input:  content (str) — plain text of the document
            provider (LLMProvider)
    Output: YAML frontmatter string (between --- markers)
    """
    try:
        response = provider.complete_fast(
            _HEADER_PROMPT.format(content=content), max_tokens=600
        )
        # Extract YAML block — may not start at position 0 if model prepends reasoning
        fm_match = re.search(r"(---\n.*?title:.*?\n---)", response, re.DOTALL)
        if fm_match and "type:" in fm_match.group(1):
            return fm_match.group(1)
        if response.startswith("---") and "title:" in response and "type:" in response:
            return response
        logger.warning("LLM returned unexpected header format, using fallback")
        return _FALLBACK_HEADER
    except Exception as exc:
        logger.error("Header generation failed: %s", exc)
        return _FALLBACK_HEADER


def redact_names(text: str, provider: LLMProvider) -> str:
    """Replace person names in text with role-based placeholders.

    Input:  raw story text (str), provider (LLMProvider)
    Output: text with person names replaced, e.g. "John" → "[DIRECT REPORT]"
            Returns original text unchanged if the call fails.
    """
    try:
        return provider.complete_fast(
            _REDACT_PROMPT.format(text=text), max_tokens=4096
        )
    except Exception as exc:
        logger.warning("Name redaction failed, using original text: %s", exc)
        return text


class IngestState:
    """Tracks which inbox files have been processed to avoid re-processing.

    State is persisted as JSON: { filename: checksum }
    Change detection: if checksum changes, file is treated as new.
    """

    def __init__(self, state_path: str):
        self._path = state_path
        self._state: Dict[str, str] = {}
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    self._state = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._state = {}

    def _save(self):
        with open(self._path, "w") as f:
            json.dump(self._state, f, indent=2)

    def is_processed(self, filename: str, checksum: Optional[str] = None) -> bool:
        """Return True if filename has been processed with the given checksum."""
        if filename not in self._state:
            return False
        if checksum is None:
            return True
        return self._state[filename] == checksum

    def mark_processed(self, filename: str, checksum: str):
        self._state[filename] = checksum
        self._save()

    def list_processed(self) -> List[str]:
        return list(self._state.keys())


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter fields from a story file.

    Handles both single-line and multi-line (block scalar) summary fields.
    Returns a dict with title, type, keywords, summary, relevance_hint.
    """
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not fm_match:
        return {}

    fm = fm_match.group(1)

    title = re.search(r"^title:\s*(.+)$", fm, re.MULTILINE)
    ftype = re.search(r"^type:\s*(.+)$", fm, re.MULTILINE)
    keywords = re.search(r"^keywords:\s*(.+)$", fm, re.MULTILINE)
    hint = re.search(r"^relevance_hint:\s*(.+)$", fm, re.MULTILINE)

    # Summary may be a YAML block scalar (summary: >\n  line1\n  line2)
    # or inline (summary: single line text)
    summary_text = ""
    summary_match = re.search(
        r"^summary:\s*(.+?)(?=\n\w|\Z)", fm, re.MULTILINE | re.DOTALL
    )
    if summary_match:
        raw = summary_match.group(1).strip()
        if raw.startswith(">"):
            # block scalar — strip the > and dedent continuation lines
            raw = raw[1:].strip()
        # Collapse internal newlines and extra whitespace
        summary_text = " ".join(raw.split())

    return {
        "title": title.group(1).strip() if title else "",
        "type": ftype.group(1).strip() if ftype else "other",
        "keywords": keywords.group(1).strip() if keywords else "",
        "summary": summary_text,
        "relevance_hint": hint.group(1).strip() if hint else "",
    }


def _build_index(stories_dir: str, output_path: str):
    """Scan stories/ and build stories_index.md.

    Each entry includes the full STAR summary so the selection pass has
    enough signal to choose relevant stories without re-reading source files.
    """
    entries = []
    for fname in sorted(os.listdir(stories_dir)):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(stories_dir, fname)
        try:
            content = open(fpath, encoding="utf-8").read()
            fm = _parse_frontmatter(content)
            entries.append({
                "file": fname,
                "title": fm.get("title") or fname,
                "type": fm.get("type", "other"),
                "keywords": fm.get("keywords", ""),
                "summary": fm.get("summary", ""),
                "relevance_hint": fm.get("relevance_hint", ""),
            })
        except Exception as exc:
            logger.warning("Could not index %s: %s", fname, exc)

    lines = [
        "# Stories Index",
        "",
        f"Total: {len(entries)} files",
        "",
        "---",
        "",
    ]
    for e in entries:
        lines += [
            f"## {e['title']}",
            f"**File**: `{e['file']}`  |  **Type**: {e['type']}",
            f"**Keywords**: {e['keywords']}",
        ]
        if e["summary"]:
            lines.append(f"**Summary**: {e['summary']}")
        if e["relevance_hint"]:
            lines.append(f"**When to use**: {e['relevance_hint']}")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info("Index rebuilt: %d entries → %s", len(entries), output_path)
    return len(entries)


class StoryIngester:
    """Orchestrates inbox → review → stories pipeline.

    Input files:  inbox_dir/  (.docx, .txt, .md)
    Review files: review_dir/ (.md with YAML frontmatter + original content)
    Final files:  stories_dir/ (.md, overwritten daily from review/)
    Index:        profile_dir/stories_index.md
    State:        profile_dir/.ingest_state.json
    """

    def __init__(
        self,
        inbox_dir: str,
        review_dir: str,
        stories_dir: str,
        profile_dir: str,
        provider: LLMProvider,
    ):
        self.inbox_dir = inbox_dir
        self.review_dir = review_dir
        self.stories_dir = stories_dir
        self.profile_dir = profile_dir
        self.provider = provider
        self._state = IngestState(os.path.join(profile_dir, ".ingest_state.json"))

        for d in [inbox_dir, review_dir, stories_dir]:
            os.makedirs(d, exist_ok=True)

    def process_inbox(self) -> dict:
        """Scan inbox/ for new or changed files and generate review/ drafts.

        Output: { processed: int, skipped: int, errors: int }
        """
        processed = skipped = errors = 0

        for fname in os.listdir(self.inbox_dir):
            src = os.path.join(self.inbox_dir, fname)
            if not os.path.isfile(src):
                continue

            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                logger.info("Skipping unsupported file: %s", fname)
                skipped += 1
                continue

            checksum = _file_checksum(src)
            if self._state.is_processed(fname, checksum):
                logger.debug("Already processed (unchanged): %s", fname)
                skipped += 1
                continue

            try:
                logger.info("Processing: %s", fname)
                text = extract_text_from_file(src)
                text = redact_names(text, self.provider)
                header = generate_header(text, self.provider)

                # Save to review/ as .md regardless of source extension
                stem = os.path.splitext(fname)[0]
                out_path = os.path.join(self.review_dir, f"{stem}.md")
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(header)
                    f.write("\n\n")
                    f.write(text)

                self._state.mark_processed(fname, checksum)
                processed += 1
                logger.info("→ review/%s.md", stem)

            except Exception as exc:
                logger.error("Failed to process %s: %s", fname, exc)
                errors += 1

        return {"processed": processed, "skipped": skipped, "errors": errors}

    def finalize(self) -> dict:
        """Copy all review/ files to stories/ (overwrite) and rebuild the index.

        Output: { copied: int, index_entries: int }
        """
        copied = 0
        for fname in os.listdir(self.review_dir):
            if not fname.endswith(".md"):
                continue
            src = os.path.join(self.review_dir, fname)
            dst = os.path.join(self.stories_dir, fname)
            shutil.copy2(src, dst)
            copied += 1

        index_path = os.path.join(self.profile_dir, "stories_index.md")
        count = _build_index(self.stories_dir, index_path)

        logger.info("Finalized: %d files copied, %d index entries", copied, count)
        return {"copied": copied, "index_entries": count}
