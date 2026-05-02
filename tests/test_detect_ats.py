"""Unit tests for pipeline/detect_ats.py."""
import pytest
from unittest.mock import patch, MagicMock

from pipeline.detect_ats import _slug_candidates, detect_ats


class TestSlugCandidates:
    def test_simple_name_produces_plain_and_hyphenated(self):
        slugs = _slug_candidates("CoreWeave")
        assert "coreweave" in slugs
        assert "coreweave" == slugs[0]  # plain first

    def test_multi_word_company(self):
        slugs = _slug_candidates("DoorDash USA")
        assert "doordashusa" in slugs
        assert "doordash-usa" in slugs

    def test_suffixes_appended(self):
        slugs = _slug_candidates("Acme")
        assert "acmeusa" in slugs
        assert "acmeinc" in slugs
        assert "acmeai" in slugs
        assert "acmehq" in slugs

    def test_no_duplicates(self):
        slugs = _slug_candidates("Acme")
        assert len(slugs) == len(set(slugs))

    def test_special_chars_stripped(self):
        slugs = _slug_candidates("Stripe, Inc.")
        assert "stripeinc" in slugs

    def test_plain_slug_is_first(self):
        slugs = _slug_candidates("OpenAI")
        assert slugs[0] == "openai"


class TestDetectAtsKnownCustom:
    def test_google_returns_google_ats(self):
        result = detect_ats("Google")
        assert result["ats"] == "google"
        assert result["board_slug"] is None
        assert result["error"] is None

    def test_apple_returns_apple_ats(self):
        result = detect_ats("Apple")
        assert result["ats"] == "apple"
        assert result["board_slug"] is None

    def test_meta_returns_meta_ats(self):
        result = detect_ats("Meta")
        assert result["ats"] == "meta"

    def test_facebook_alias_returns_meta(self):
        result = detect_ats("Facebook")
        assert result["ats"] == "meta"

    def test_known_company_case_insensitive(self):
        result = detect_ats("GOOGLE")
        assert result["ats"] == "google"

    def test_known_company_with_whitespace(self):
        result = detect_ats("  Apple  ")
        assert result["ats"] == "apple"

    def test_known_company_skips_http_calls(self):
        with patch("pipeline.detect_ats.requests.get") as mock_get:
            detect_ats("Google")
            mock_get.assert_not_called()


class TestDetectAtsGreenhouseSuccess:
    def _greenhouse_ok_response(self):
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = {"jobs": [{"id": 1}]}
        return mock

    def _not_found_response(self):
        mock = MagicMock()
        mock.status_code = 404
        return mock

    def test_greenhouse_detected_on_200(self):
        ok = self._greenhouse_ok_response()
        not_found = self._not_found_response()

        def fake_get(url, **kwargs):
            if "greenhouse" in url and "coreweave" in url:
                return ok
            return not_found

        with patch("pipeline.detect_ats.requests.get", side_effect=fake_get):
            result = detect_ats("CoreWeave")

        assert result["ats"] == "greenhouse"
        assert result["board_slug"] == "coreweave"
        assert result["error"] is None

    def test_tried_list_populated(self):
        not_found = self._not_found_response()
        with patch("pipeline.detect_ats.requests.get", return_value=not_found):
            result = detect_ats("UnknownCo")
        assert len(result["tried"]) > 0
        assert all(":" in t for t in result["tried"])


class TestDetectAtsAshbySuccess:
    def test_ashby_detected_on_200(self):
        def fake_get(url, **kwargs):
            mock = MagicMock()
            if "ashbyhq" in url and "acme" in url:
                mock.status_code = 200
            else:
                mock.status_code = 404
            return mock

        with patch("pipeline.detect_ats.requests.get", side_effect=fake_get):
            result = detect_ats("Acme")

        assert result["ats"] == "ashby"
        assert result["board_slug"] == "acme"


class TestDetectAtsLeverSuccess:
    def test_lever_detected_on_200_with_list(self):
        def fake_get(url, **kwargs):
            mock = MagicMock()
            if "lever" in url and "stripe" in url:
                mock.status_code = 200
                mock.json.return_value = [{"id": "abc"}]
            else:
                mock.status_code = 404
                mock.json.return_value = {}
            return mock

        with patch("pipeline.detect_ats.requests.get", side_effect=fake_get):
            result = detect_ats("Stripe")

        assert result["ats"] == "lever"
        assert "stripe" in result["board_slug"]


class TestDetectAtsFailure:
    def test_no_match_returns_none_ats(self):
        mock = MagicMock()
        mock.status_code = 404
        with patch("pipeline.detect_ats.requests.get", return_value=mock):
            result = detect_ats("FakeCompanyXYZ123")

        assert result["ats"] is None
        assert result["board_slug"] is None
        assert result["error"] is not None
        assert "FakeCompanyXYZ123" in result["error"]

    def test_network_error_handled_gracefully(self):
        with patch("pipeline.detect_ats.requests.get", side_effect=Exception("timeout")):
            result = detect_ats("SomeCompany")

        assert result["ats"] is None
        assert result["error"] is not None

    def test_greenhouse_priority_over_ashby(self):
        """When both greenhouse and ashby match, greenhouse wins."""
        def fake_get(url, **kwargs):
            mock = MagicMock()
            if "greenhouse" in url and "acme" in url:
                mock.status_code = 200
                mock.json.return_value = {"jobs": []}
            elif "ashby" in url and "acme" in url:
                mock.status_code = 200
            else:
                mock.status_code = 404
                mock.json.return_value = {}
            return mock

        with patch("pipeline.detect_ats.requests.get", side_effect=fake_get):
            result = detect_ats("Acme")

        assert result["ats"] == "greenhouse"
