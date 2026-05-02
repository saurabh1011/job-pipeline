"""Unit tests for pipeline/analyzer.py."""
import json
import pytest
from unittest.mock import MagicMock, patch

from pipeline.analyzer import Analyzer, AnalysisResult, _clean_json
from pipeline.llm import LLMProvider


# ── Fixtures ────────────────────────────────────────────────────────────────

SAMPLE_REQUIREMENTS = [
    "Lead a team of 8–12 engineers across multiple pods",
    "Define and own the technical roadmap",
    "5+ years of engineering management experience",
]

SAMPLE_EVALUATIONS = [
    {
        "requirement": "Lead a team of 8–12 engineers across multiple pods",
        "fit": "Strong",
        "evidence": "Led a team of 10 engineers at Acme Corp.",
        "resume_suggestion": None,
    },
    {
        "requirement": "Define and own the technical roadmap",
        "fit": "Partial",
        "evidence": "Contributed to roadmap at Acme.",
        "resume_suggestion": "Add a bullet about roadmap ownership.",
    },
    {
        "requirement": "5+ years of engineering management experience",
        "fit": "Gap",
        "evidence": "3 years of EM experience visible in profile.",
        "resume_suggestion": "Highlight total years including TL tenure.",
    },
]

SAMPLE_RESUME_SUGGESTIONS = [
    "Add metrics to team leadership bullets.",
    "Include roadmap ownership language.",
]


def make_mock_provider(extract_response, evaluate_response):
    provider = MagicMock(spec=LLMProvider)
    provider.complete_json.side_effect = [extract_response, evaluate_response]
    return provider


def make_job(**kwargs):
    return {
        "company": "Acme",
        "title": "Engineering Manager",
        "description": "Lead teams. Define roadmap.",
        **kwargs,
    }


def make_profile():
    return {
        "name": "Jane Doe",
        "summary": "10 years of engineering management.",
        "experience": [],
        "education": [],
        "skills": [],
    }


# ── _clean_json ─────────────────────────────────────────────────────────────

class TestCleanJson:
    def test_strips_markdown_fences(self):
        raw = "```json\n[1,2,3]\n```"
        assert _clean_json(raw) == "[1,2,3]"

    def test_strips_plain_code_fence(self):
        raw = "```\n{\"a\":1}\n```"
        assert _clean_json(raw) == '{"a":1}'

    def test_passthrough_for_plain_json(self):
        raw = '{"key": "value"}'
        assert _clean_json(raw) == raw

    def test_strips_leading_trailing_whitespace(self):
        raw = "  [1, 2, 3]  "
        assert _clean_json(raw).strip() == "[1, 2, 3]"


# ── Analyzer.analyze — happy path ────────────────────────────────────────────

class TestAnalyzerHappyPath:
    def _run(self, extract_resp, evaluate_resp, job=None, profile=None):
        provider = make_mock_provider(extract_resp, evaluate_resp)
        analyzer = Analyzer(provider)
        with patch("pipeline.profile.ProfileLoader") as MockLoader:
            instance = MockLoader.return_value
            instance.full_text.return_value = "Experienced EM with 10 years."
            return analyzer.analyze(
                job or make_job(),
                profile or make_profile(),
            )

    def test_returns_analysis_result(self):
        extract = json.dumps(SAMPLE_REQUIREMENTS)
        evaluate = json.dumps({
            "evaluations": SAMPLE_EVALUATIONS,
            "resume_suggestions": SAMPLE_RESUME_SUGGESTIONS,
        })
        result = self._run(extract, evaluate)
        assert isinstance(result, AnalysisResult)

    def test_requirements_count_matches_input(self):
        extract = json.dumps(SAMPLE_REQUIREMENTS)
        evaluate = json.dumps({
            "evaluations": SAMPLE_EVALUATIONS,
            "resume_suggestions": SAMPLE_RESUME_SUGGESTIONS,
        })
        result = self._run(extract, evaluate)
        assert len(result.requirements) == len(SAMPLE_EVALUATIONS)

    def test_resume_suggestions_returned(self):
        extract = json.dumps(SAMPLE_REQUIREMENTS)
        evaluate = json.dumps({
            "evaluations": SAMPLE_EVALUATIONS,
            "resume_suggestions": SAMPLE_RESUME_SUGGESTIONS,
        })
        result = self._run(extract, evaluate)
        assert result.resume_suggestions == SAMPLE_RESUME_SUGGESTIONS

    def test_fit_values_preserved(self):
        extract = json.dumps(SAMPLE_REQUIREMENTS)
        evaluate = json.dumps({
            "evaluations": SAMPLE_EVALUATIONS,
            "resume_suggestions": [],
        })
        result = self._run(extract, evaluate)
        fits = [r["fit"] for r in result.requirements]
        assert "Strong" in fits
        assert "Partial" in fits
        assert "Gap" in fits

    def test_two_llm_calls_made(self):
        extract = json.dumps(SAMPLE_REQUIREMENTS)
        evaluate = json.dumps({
            "evaluations": SAMPLE_EVALUATIONS,
            "resume_suggestions": [],
        })
        provider = make_mock_provider(extract, evaluate)
        analyzer = Analyzer(provider)
        with patch("pipeline.profile.ProfileLoader") as MockLoader:
            MockLoader.return_value.full_text.return_value = "profile text"
            analyzer.analyze(make_job(), make_profile())
        assert provider.complete_json.call_count == 2

    def test_markdown_fenced_responses_parsed(self):
        extract = "```json\n" + json.dumps(SAMPLE_REQUIREMENTS) + "\n```"
        evaluate = "```json\n" + json.dumps({
            "evaluations": SAMPLE_EVALUATIONS,
            "resume_suggestions": SAMPLE_RESUME_SUGGESTIONS,
        }) + "\n```"
        result = self._run(extract, evaluate)
        assert len(result.requirements) == 3

    def test_log_callable_called(self):
        extract = json.dumps(SAMPLE_REQUIREMENTS)
        evaluate = json.dumps({
            "evaluations": SAMPLE_EVALUATIONS,
            "resume_suggestions": [],
        })
        provider = make_mock_provider(extract, evaluate)
        analyzer = Analyzer(provider)
        log_calls = []
        with patch("pipeline.profile.ProfileLoader") as MockLoader:
            MockLoader.return_value.full_text.return_value = "profile"
            analyzer.analyze(make_job(), make_profile(), log=log_calls.append)
        assert len(log_calls) >= 3  # at least start, extraction count, evaluation count


# ── Fit normalization ────────────────────────────────────────────────────────

class TestFitNormalization:
    def _run_with_evals(self, evals):
        provider = make_mock_provider(
            json.dumps(SAMPLE_REQUIREMENTS),
            json.dumps({"evaluations": evals, "resume_suggestions": []}),
        )
        analyzer = Analyzer(provider)
        with patch("pipeline.profile.ProfileLoader") as MockLoader:
            MockLoader.return_value.full_text.return_value = "profile"
            return analyzer.analyze(make_job(), make_profile())

    def test_lowercase_fit_normalized(self):
        evals = [{"requirement": "x", "fit": "strong", "evidence": "e", "resume_suggestion": None}]
        result = self._run_with_evals(evals)
        assert result.requirements[0]["fit"] == "Strong"

    def test_unknown_fit_becomes_partial(self):
        evals = [{"requirement": "x", "fit": "unknown_value", "evidence": "e", "resume_suggestion": None}]
        result = self._run_with_evals(evals)
        assert result.requirements[0]["fit"] == "Partial"

    def test_all_valid_fits_preserved(self):
        evals = [
            {"requirement": "a", "fit": "Strong", "evidence": "e", "resume_suggestion": None},
            {"requirement": "b", "fit": "Partial", "evidence": "e", "resume_suggestion": None},
            {"requirement": "c", "fit": "Gap", "evidence": "e", "resume_suggestion": None},
        ]
        result = self._run_with_evals(evals)
        assert [r["fit"] for r in result.requirements] == ["Strong", "Partial", "Gap"]


# ── Error handling ───────────────────────────────────────────────────────────

class TestAnalyzerErrorHandling:
    def test_bad_extract_response_raises(self):
        provider = make_mock_provider("not json at all", "irrelevant")
        analyzer = Analyzer(provider)
        with patch("pipeline.profile.ProfileLoader") as MockLoader:
            MockLoader.return_value.full_text.return_value = "profile"
            with pytest.raises(ValueError, match="Could not extract requirements"):
                analyzer.analyze(make_job(), make_profile())

    def test_bad_evaluate_response_returns_empty(self):
        provider = make_mock_provider(
            json.dumps(SAMPLE_REQUIREMENTS),
            "this is not json",
        )
        analyzer = Analyzer(provider)
        with patch("pipeline.profile.ProfileLoader") as MockLoader:
            MockLoader.return_value.full_text.return_value = "profile"
            result = analyzer.analyze(make_job(), make_profile())
        assert result.requirements == []
        assert result.resume_suggestions == []

    def test_empty_requirements_list_raises(self):
        provider = make_mock_provider(json.dumps([]), "irrelevant")
        analyzer = Analyzer(provider)
        with patch("pipeline.profile.ProfileLoader") as MockLoader:
            MockLoader.return_value.full_text.return_value = "profile"
            with pytest.raises(ValueError):
                analyzer.analyze(make_job(), make_profile())
