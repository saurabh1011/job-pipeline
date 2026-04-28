"""Content generator — produces cover letter, tailored resume, and diff.

Input:
    job:         normalized job dict (from fetcher)
    profile:     profile dict (from ProfileLoader.load())

Output (GeneratedContent):
    cover_letter:    str  — markdown cover letter
    tailored_resume: str  — markdown resume tailored to this job
    resume_diff:     str  — unified diff between base and tailored resume
    output_dir:      str  — path where files were saved

Files saved to output/{Company}_{job_id}/:
    cover_letter.md
    resume_tailored.md
    resume_diff.patch
"""
import difflib
import logging
import os
from dataclasses import dataclass

from pipeline.llm import LLMProvider

logger = logging.getLogger(__name__)

_COVER_LETTER_PROMPT = """You are writing a cover letter on behalf of Saurabh Shah. Match his exact writing style based on the style guide below.

## His Writing Style Guide
{style_guide}

## Candidate Profile
{profile_text}

## Target Job
Company: {company}
Title: {title}
Location: {location}

Job Description:
{description}

## Instructions
- Follow the four-paragraph framework from the style guide exactly
- Address to "To the hiring team at {company},"
- Mirror the company's specific challenges by naming them explicitly and linking to a prior accomplishment
- Use strong verbs: led, owned, modernized, unified, incubated — never "helped" or "contributed to"
- Include specific metrics in every paragraph
- CRITICAL: Only use facts, experiences, and skills explicitly stated in the candidate profile above. Do not invent, infer, or embellish anything.

Return ONLY the cover letter text, no other commentary."""

_RESUME_PROMPT = """You are an expert resume writer specializing in Engineering Manager and Director-level roles.

Tailor the following resume for a specific job application. Reorder, reword, and emphasize experiences most relevant to this role. Do not fabricate experience — only reorganize and reframe what exists.

## Original Resume
{resume}

## Additional Context
{experience}
{google_docs_section}

## Target Job
Company: {company}
Title: {title}

Key Requirements from Job Description:
{description}

## Instructions
- Return the complete tailored resume in Markdown format
- Move most relevant experiences to the top of each section
- Adjust bullet points to mirror language from the job description where authentic
- Emphasize leadership scope, team size, and impact metrics
- Keep the same overall structure (sections, timeline) — only adjust emphasis and wording
- CRITICAL: Do NOT invent, infer, or add any experience, skill, technology, or accomplishment not explicitly present in the original resume or additional context above. Only reword and reorder what is already there.
- CRITICAL: Do NOT borrow terminology from the job description and insert it into the resume as if it describes the candidate's background. For example, if the job description uses the word "agentic" but the candidate's resume does not, do NOT add "agentic" to the resume.

Return ONLY the tailored resume in Markdown, no other commentary."""


@dataclass
class GeneratedContent:
    cover_letter: str
    tailored_resume: str
    resume_diff: str
    output_dir: str


class ContentGenerator:
    def __init__(self, provider: LLMProvider, output_dir: str = "output"):
        self._provider = provider
        self.output_dir = output_dir

    def _call_claude(self, prompt: str, max_tokens: int = 2048) -> str:
        return self._provider.complete(prompt, max_tokens=max_tokens)

    def _build_diff(self, base: str, tailored: str, company: str, job_id: str) -> str:
        """Generate a unified diff between base and tailored resume."""
        base_lines = base.splitlines(keepends=True)
        tailored_lines = tailored.splitlines(keepends=True)
        diff = list(difflib.unified_diff(
            base_lines,
            tailored_lines,
            fromfile=f"resume_base.md",
            tofile=f"resume_tailored_{company}_{job_id}.md",
            lineterm="",
        ))
        return "".join(diff)

    def _check_resume_hallucinations(self, tailored: str, base_resume: str, jd: str) -> list[str]:
        """Return list of words found in tailored resume that appear in JD but not in base resume."""
        import re
        base_words = set(w.lower() for w in re.findall(r"[a-zA-Z]+", base_resume))
        jd_words = set(w.lower() for w in re.findall(r"[a-zA-Z]+", jd))
        tailored_words = set(w.lower() for w in re.findall(r"[a-zA-Z]+", tailored))
        jd_only = jd_words - base_words
        inserted = jd_only & tailored_words
        # Only flag meaningful words (length > 5, not common English)
        common = {"their", "there", "where", "which", "these", "those", "about", "would", "could",
                  "should", "after", "before", "while", "other", "under", "every", "being", "having",
                  "using", "build", "built", "drive", "driven", "large", "small", "cross", "first",
                  "teams", "level", "based", "align", "bring", "makes", "makes", "world", "place",
                  "needs", "help", "work", "works", "team", "role", "each", "into", "with", "from",
                  "that", "this", "your", "have", "been", "will", "also", "more", "such", "than"}
        flagged = sorted(w for w in inserted if len(w) > 5 and w not in common)
        return flagged

    def generate(self, job: dict, profile: dict) -> GeneratedContent:
        from pipeline.profile import ProfileLoader
        loader = ProfileLoader()
        profile_text = loader.full_text(profile)

        style_guide_path = os.path.join("profile", "cover_letter_style_guide.md")
        style_guide = ""
        if os.path.exists(style_guide_path):
            with open(style_guide_path, encoding="utf-8") as f:
                style_guide = f.read()

        # Generate cover letter
        cover_prompt = _COVER_LETTER_PROMPT.format(
            style_guide=style_guide,
            profile_text=profile_text,
            company=job["company"],
            title=job["title"],
            location=job["location"],
            description=job["description"],
        )
        cover_letter = self._call_claude(cover_prompt, max_tokens=1024)

        # Use base resume as-is (no tailoring)
        tailored_resume = profile.get("resume", "")
        resume_diff = ""

        # Save files
        job_dir = os.path.join(self.output_dir, f"{job['company']}_{job['job_id']}")
        os.makedirs(job_dir, exist_ok=True)

        with open(os.path.join(job_dir, "cover_letter.md"), "w", encoding="utf-8") as f:
            f.write(f"# Cover Letter — {job['title']} at {job['company']}\n\n")
            f.write(cover_letter)

        with open(os.path.join(job_dir, "resume_tailored.md"), "w", encoding="utf-8") as f:
            f.write(tailored_resume)

        with open(os.path.join(job_dir, "resume_diff.patch"), "w", encoding="utf-8") as f:
            f.write(resume_diff)

        logger.info("Generated materials for %s/%s in %s", job["company"], job["job_id"], job_dir)

        return GeneratedContent(
            cover_letter=cover_letter,
            tailored_resume=tailored_resume,
            resume_diff=resume_diff,
            output_dir=job_dir,
        )
