"""Deep job analysis: two-call LLM approach for granular requirement evaluation.

Call 1 — Extract: given a job description, produce a structured list of requirements.
Call 2 — Evaluate: given the extracted requirements + candidate profile, evaluate
         each requirement and produce resume suggestions.

This is an on-demand feature for jobs the candidate cares about — not run in batch.

Input:
    job:     normalized job dict (from store)
    profile: profile dict (from ProfileLoader.load())

Output (AnalysisResult):
    requirements:        list of {requirement, fit, evidence, resume_suggestion}
    resume_suggestions:  list of actionable resume improvement strings
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from pipeline.llm import LLMProvider

logger = logging.getLogger(__name__)

_EXTRACT_PROMPT = """You are analyzing a job posting to identify what the role requires.

## Job Description
Company: {company}
Title: {title}

{description}

## Instructions
Extract all key requirements and responsibilities from this posting.
Include: core responsibilities, required experience/skills, management expectations, technical requirements.
Exclude: company boilerplate, benefits, equal-opportunity statements.

Return ONLY a JSON array of strings. Each string is one clear, atomic requirement.
Aim for 8–15 items. Be specific — include numbers/scope where stated (e.g. "manage a team of 8–12 engineers").

Example format:
["Lead a team of 8–12 engineers across multiple pods", "Define and own the technical roadmap", "5+ years of engineering management experience"]

Return ONLY the JSON array, no other text."""


_EVALUATE_PROMPT = """You are a career coach evaluating a candidate against specific job requirements.

## Candidate Profile
{profile_text}

## Job Context
Company: {company}
Title: {title}

## Requirements to Evaluate
{requirements_json}

## Instructions
For each requirement, evaluate how well the candidate's profile demonstrates it.

Return ONLY this JSON object:
{{
  "evaluations": [
    {{
      "requirement": "<exact requirement text from the list>",
      "fit": "Strong",
      "evidence": "<one specific sentence from the profile — name the company/project>",
      "resume_suggestion": null
    }},
    {{
      "requirement": "<requirement text>",
      "fit": "Partial",
      "evidence": "<what they have that's related>",
      "resume_suggestion": "<specific thing to add or reframe in the resume>"
    }},
    {{
      "requirement": "<requirement text>",
      "fit": "Gap",
      "evidence": "<why this is a gap>",
      "resume_suggestion": "<how to address this — adjacent experience to highlight, or honest gap>"
    }}
  ],
  "resume_suggestions": [
    "<top 3–5 actionable changes to make to the resume specifically for this role>"
  ]
}}

Fit definitions:
  Strong  — profile clearly demonstrates this requirement
  Partial — candidate has related experience but not an exact match
  Gap     — no clear evidence in the profile; this is a real gap

Be specific. Reference actual experience, projects, and companies from the profile.
Return ONLY the JSON object, no other text."""


@dataclass
class AnalysisResult:
    requirements: List[dict] = field(default_factory=list)
    resume_suggestions: List[str] = field(default_factory=list)


def _clean_json(raw: str) -> str:
    """Strip markdown fences and find the first JSON structure."""
    cleaned = re.sub(r"^```[a-z]*\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned


class Analyzer:
    def __init__(self, provider: LLMProvider):
        self._provider = provider

    def analyze(self, job: dict, profile: dict, log=None) -> AnalysisResult:
        """Run two-call deep analysis for a single job.

        Input:
            job     — job dict from store (needs: company, title, description)
            profile — profile dict from ProfileLoader.load()
            log     — optional callable for task-drawer progress messages

        Output: AnalysisResult with per-requirement evaluation + resume suggestions
        """
        from pipeline.profile import ProfileLoader
        loader = ProfileLoader()
        profile_text = loader.full_text(profile)

        company = job.get("company", "")
        title = job.get("title", "")
        description = job.get("description", "")

        # ── Call 1: extract requirements ──────────────────────────────────────
        if log:
            log("  [1/2] Extracting requirements from job description…")

        extract_prompt = _EXTRACT_PROMPT.format(
            company=company,
            title=title,
            description=description,
        )
        raw1 = self._provider.complete_json(extract_prompt, max_tokens=800)

        requirements_list = []
        try:
            cleaned1 = _clean_json(raw1)
            m = re.search(r"\[[\s\S]*\]", cleaned1)
            if m:
                requirements_list = json.loads(m.group())
        except (json.JSONDecodeError, ValueError):
            logger.warning("Failed to parse requirements extraction: %s", raw1[:200])

        if not requirements_list:
            raise ValueError("Could not extract requirements from job description")

        if log:
            log(f"  → {len(requirements_list)} requirements extracted")

        # ── Call 2: evaluate each requirement against profile ─────────────────
        if log:
            log("  [2/2] Evaluating fit against your profile…")

        evaluate_prompt = _EVALUATE_PROMPT.format(
            profile_text=profile_text,
            company=company,
            title=title,
            requirements_json=json.dumps(requirements_list, indent=2),
        )
        raw2 = self._provider.complete_json(evaluate_prompt, max_tokens=2500)

        evaluations = []
        resume_suggestions = []
        try:
            cleaned2 = _clean_json(raw2)
            m = re.search(r"\{[\s\S]*\}", cleaned2)
            if m:
                data = json.loads(m.group())
                evaluations = data.get("evaluations", [])
                resume_suggestions = data.get("resume_suggestions", [])
        except (json.JSONDecodeError, ValueError):
            logger.warning("Failed to parse evaluation response: %s", raw2[:200])

        # Normalize fit values
        for ev in evaluations:
            fit = str(ev.get("fit", "")).strip().capitalize()
            if fit not in ("Strong", "Partial", "Gap"):
                fit = "Partial"
            ev["fit"] = fit

        if log:
            counts = {f: sum(1 for e in evaluations if e.get("fit") == f)
                      for f in ("Strong", "Partial", "Gap")}
            log(f"  → Strong: {counts['Strong']}  Partial: {counts['Partial']}  Gap: {counts['Gap']}")

        return AnalysisResult(
            requirements=evaluations,
            resume_suggestions=resume_suggestions,
        )
