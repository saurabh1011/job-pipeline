"""JobScorer — scores a job against the candidate's profile using an LLM.

Input:
    job:         normalized job dict (from fetcher)
    profile:     profile dict (from ProfileLoader.load())
    preferences: preferences dict (from preferences.yaml)

Output (ScoreResult):
    score:          int   — raw LLM score 1-10
    adjusted_score: int   — score after location penalty
    location_penalty: int — penalty applied based on location
    summary:        str   — 2-3 sentence match rationale
    strengths:      list  — bullet points of alignment areas
    gaps:           list  — bullet points of gaps
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import List

from pipeline.llm import LLMProvider

logger = logging.getLogger(__name__)


def _parse_score(raw) -> int:
    """Extract an integer 1-10 from various model output formats.

    Handles: int, float, "8", "8/10", "4.5/5 (Strong Hire)", "9 out of 10"
    Normalizes scores on a /5 scale to /10.
    """
    s = str(raw).strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+)", s)
    if m:
        numerator, denominator = float(m.group(1)), float(m.group(2))
        score = round(numerator * 10 / denominator)
        return max(1, min(10, score))
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if m:
        return max(1, min(10, round(float(m.group(1)))))
    return 1

_SCORE_PROMPT = """You are an expert recruiter and career coach. Evaluate how well a candidate matches a job posting.

## Candidate Profile
{profile_text}

## Job Posting
Company: {company}
Title: {title}
Location: {location}

{description}

## Instructions
Return ONLY a JSON object with these exact keys:
{{
  "score": <integer 1-10>,
  "summary": "<2-3 sentence match rationale>",
  "strengths": ["<one short phrase each, max 3 items>"],
  "gaps": ["<one short phrase each, max 3 items>"],
  "location_note": "<one sentence>"
}}

Scoring guide:
- 9-10: Exceptional match, candidate clearly qualified and role aligns with trajectory
- 7-8:  Strong match, minor gaps that are bridgeable
- 5-6:  Moderate match, significant gaps or misalignment
- 1-4:  Weak match, fundamental mismatch

Focus on leadership scope, team size, technical domain, and seniority level.
Be concise. Return ONLY the JSON object, no other text."""


@dataclass
class ScoreResult:
    score: int
    adjusted_score: int
    location_penalty: int
    summary: str
    strengths: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    location_note: str = ""

    def meets_threshold(self, threshold: int) -> bool:
        return self.adjusted_score >= threshold


def _compute_location_penalty(location: str, preferences: dict) -> int:
    location_lower = location.lower()
    penalties = preferences.get("location_penalties", {"preferred": 0, "acceptable": 1, "other": 3})

    preferred = [loc.lower() for loc in preferences.get("preferred_locations", [])]
    acceptable = [loc.lower() for loc in preferences.get("acceptable_locations", [])]

    for pref in preferred:
        if pref in location_lower:
            return penalties.get("preferred", 0)

    for acc in acceptable:
        if acc in location_lower:
            return penalties.get("acceptable", 1)

    return penalties.get("other", 3)


class JobScorer:
    def __init__(self, provider: LLMProvider):
        self._provider = provider

    def _call_llm(self, prompt: str) -> str:
        return self._provider.complete_json(prompt, max_tokens=1024)

    def score(self, job: dict, profile: dict, preferences: dict) -> ScoreResult:
        from pipeline.profile import ProfileLoader
        loader = ProfileLoader()
        profile_text = loader.full_text(profile)

        prompt = _SCORE_PROMPT.format(
            profile_text=profile_text,
            company=job.get("company", ""),
            title=job.get("title", ""),
            location=job.get("location", ""),
            description=job.get("description", ""),
        )

        raw = self._call_llm(prompt)
        return self._parse_response(raw, job, preferences)

    def _parse_response(self, raw: str, job: dict, preferences: dict) -> ScoreResult:
        penalty = _compute_location_penalty(job.get("location", ""), preferences)

        cleaned = re.sub(r"^```[a-z]*\s*", "", raw.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        json_match = re.search(r"\{[\s\S]*\}", cleaned)
        if json_match:
            try:
                data = json.loads(json_match.group())

                raw_score = (
                    data.get("score")
                    or data.get("match_score")
                    or data.get("rating")
                    or 1
                )
                score = _parse_score(raw_score)

                return ScoreResult(
                    score=score,
                    adjusted_score=max(1, score - penalty),
                    location_penalty=penalty,
                    summary=data.get("summary") or data.get("overall_assessment") or "",
                    strengths=data.get("strengths", []),
                    gaps=data.get("gaps", []),
                    location_note=data.get("location_note", ""),
                )
            except (json.JSONDecodeError, ValueError):
                pass

        logger.warning("Could not parse score response: %s", raw[:200])
        return ScoreResult(
            score=1,
            adjusted_score=max(1, 1 - penalty),
            location_penalty=penalty,
            summary="Could not assess match automatically. Please review manually.",
            strengths=[],
            gaps=[],
        )
