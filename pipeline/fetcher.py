"""Job fetchers for each company ATS type.

Each fetcher.fetch(preferences) returns a list of normalized job dicts:
    {
        job_id:     str   — ATS-assigned ID (stringified)
        company:    str   — company name
        title:      str   — job title
        location:   str   — location string
        url:        str   — canonical job listing URL
        apply_url:  str   — direct apply URL (same as url for Greenhouse)
        description: str  — plain-text job description (HTML stripped)
    }
"""
import copy
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def _strip_html(html: str) -> str:
    """Remove HTML tags, preserving block-level structure as newlines."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _matches_title(title: str, preferences: dict) -> bool:
    """Return True if title matches any include keyword and no exclude keyword.

    Exclude keywords use word-boundary matching so that e.g. "Software Engineer"
    does not incorrectly exclude "Software Engineering Manager".
    """
    title_lower = title.lower()
    include_kws = [kw.lower() for kw in preferences.get("title_keywords", [])]
    exclude_kws = [kw.lower() for kw in preferences.get("title_exclude_keywords", [])]

    has_include = any(kw in title_lower for kw in include_kws)
    has_exclude = any(re.search(r'\b' + re.escape(kw) + r'\b', title_lower) for kw in exclude_kws)
    return has_include and not has_exclude


def _matches_location(location: str, preferences: dict) -> bool:
    """Return False if the location is excluded or violates us_only.

    location_filter: allowlist — if set, location must contain at least one term.
    excluded_location_keywords: denylist — location must not contain any term.
    us_only: rejects locations formatted as "CC, ..." where CC is a
    2-letter country code that is not US (Amazon-style location strings).
    """
    location_lower = location.lower()
    location_filter = [kw.lower() for kw in preferences.get("location_filter", [])]
    if location_filter and not any(kw in location_lower for kw in location_filter):
        return False
    excluded = [kw.lower() for kw in preferences.get("excluded_location_keywords", [])]
    if excluded and any(kw in location_lower for kw in excluded):
        return False
    if preferences.get("us_only", False):
        if re.match(r'^[A-Z]{2},\s', location) and not location.startswith("US,"):
            return False
    return True


def _build_company_prefs(company: dict, global_prefs: dict) -> dict:
    """Return a deep copy of global_prefs with company-level overrides applied.

    Supported overrides: title_keywords, location_filter.
    """
    prefs = copy.deepcopy(global_prefs)
    if "title_keywords" in company:
        prefs["title_keywords"] = list(company["title_keywords"])
    if "location_filter" in company:
        prefs["location_filter"] = list(company["location_filter"])
    return prefs


class GreenhouseFetcher:
    """Fetches jobs from the public Greenhouse Jobs Board API.

    API: GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
    No authentication required.
    """

    _BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"

    def __init__(self, board_slug: str, company_name: str):
        self.board_slug = board_slug
        self.company_name = company_name

    def fetch(self, preferences: dict) -> List[dict]:
        url = self._BASE_URL.format(slug=self.board_slug)
        try:
            resp = requests.get(url, params={"content": "true"}, timeout=15)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Greenhouse fetch failed for %s: %s", self.company_name, exc)
            return []

        raw_jobs = resp.json().get("jobs", [])
        results = []
        for job in raw_jobs:
            title = job.get("title", "")
            if not _matches_title(title, preferences):
                continue
            location = job.get("location", {}).get("name", "")
            if not _matches_location(location, preferences):
                logger.debug("Excluded by location: %s — %s", title, location)
                continue
            results.append({
                "job_id": str(job["id"]),
                "company": self.company_name,
                "title": title,
                "location": location,
                "url": job.get("absolute_url", ""),
                "apply_url": job.get("absolute_url", ""),
                "description": _strip_html(job.get("content", "")),
            })
        logger.info("Greenhouse/%s: %d matching jobs found", self.company_name, len(results))
        return results


class GoogleFetcher:
    """Fetches Engineering Manager roles from Google Careers.

    Google does not use Greenhouse. This fetcher calls the Google Careers
    JSON API used by their careers website.
    """

    _SEARCH_URL = "https://careers.google.com/api/jobs/jobs-v1/search/"

    def __init__(self, company_name: str = "Google"):
        self.company_name = company_name

    def fetch(self, preferences: dict) -> List[dict]:
        results = []
        # Fetch for each title keyword to maximize coverage
        seen_ids: set = set()
        for keyword in preferences.get("title_keywords", ["Engineering Manager"]):
            jobs = self._fetch_keyword(keyword, preferences)
            for job in jobs:
                if job["job_id"] not in seen_ids:
                    seen_ids.add(job["job_id"])
                    results.append(job)
        logger.info("Google: %d matching jobs found", len(results))
        return results

    def _fetch_keyword(self, keyword: str, preferences: dict) -> List[dict]:
        params = {
            "q": keyword,
            "hl": "en_US",
            "sort_by": "date",
        }
        try:
            resp = requests.get(self._SEARCH_URL, params=params, timeout=15)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Google fetch failed for keyword '%s': %s", keyword, exc)
            return []

        data = resp.json()
        jobs_raw = data.get("jobs", [])
        results = []
        for job in jobs_raw:
            title = job.get("title", "")
            if not _matches_title(title, preferences):
                continue
            locations = job.get("locations", [])
            location_str = ", ".join(locations) if locations else ""
            if not _matches_location(location_str, preferences):
                logger.debug("Excluded by location: %s — %s", title, location_str)
                continue
            job_id = str(job.get("job_id", job.get("id", "")))
            apply_url = f"https://careers.google.com/jobs/results/{job_id}"
            results.append({
                "job_id": job_id,
                "company": self.company_name,
                "title": title,
                "location": location_str,
                "url": apply_url,
                "apply_url": apply_url,
                "description": _strip_html(job.get("description", "")),
            })
        return results


class WalmartFetcher:
    """Fetches Engineering Manager roles from Walmart Careers.

    Walmart uses a custom ATS. This fetcher calls the Walmart careers
    search API endpoint.
    """

    _SEARCH_URL = "https://careers.walmart.com/api/jobs"

    def __init__(self, company_name: str = "Walmart"):
        self.company_name = company_name

    def fetch(self, preferences: dict) -> List[dict]:
        results = []
        seen_ids: set = set()
        for keyword in preferences.get("title_keywords", ["Engineering Manager"]):
            jobs = self._fetch_keyword(keyword, preferences)
            for job in jobs:
                if job["job_id"] not in seen_ids:
                    seen_ids.add(job["job_id"])
                    results.append(job)
        logger.info("Walmart: %d matching jobs found", len(results))
        return results

    def _fetch_keyword(self, keyword: str, preferences: dict) -> List[dict]:
        params = {
            "q": keyword,
            "page": 1,
            "pageSize": 50,
        }
        headers = {"User-Agent": "Mozilla/5.0 (compatible; JobPipeline/1.0)"}
        try:
            resp = requests.get(
                self._SEARCH_URL, params=params, headers=headers, timeout=15
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Walmart fetch failed for keyword '%s': %s", keyword, exc)
            return []

        try:
            data = resp.json()
        except Exception as exc:
            logger.warning("Walmart fetch failed for keyword '%s': non-JSON response: %s", keyword, exc)
            return []
        # Walmart API returns jobs under "jobPostings" or similar key
        jobs_raw = data.get("jobPostings", data.get("jobs", []))
        results = []
        for job in jobs_raw:
            title = job.get("title", job.get("jobTitle", ""))
            if not _matches_title(title, preferences):
                continue
            location = job.get("primaryLocation", job.get("location", ""))
            if not _matches_location(location, preferences):
                logger.debug("Excluded by location: %s — %s", title, location)
                continue
            job_id = str(job.get("jobId", job.get("id", "")))
            url = f"https://careers.walmart.com/us/jobs/{job_id}/job"
            results.append({
                "job_id": job_id,
                "company": self.company_name,
                "title": title,
                "location": location,
                "url": url,
                "apply_url": url,
                "description": _strip_html(job.get("jobDescription", job.get("description", ""))),
            })
        return results


class AppleFetcher:
    """Fetches Engineering Manager roles from Apple Careers.

    Apple uses their own jobs.apple.com platform with a REST search API.
    """

    _SEARCH_URL = "https://jobs.apple.com/api/role/search"

    def __init__(self, company_name: str = "Apple"):
        self.company_name = company_name

    def fetch(self, preferences: dict) -> List[dict]:
        results = []
        seen_ids: set = set()
        for keyword in preferences.get("title_keywords", ["Engineering Manager"]):
            jobs = self._fetch_keyword(keyword, preferences)
            for job in jobs:
                if job["job_id"] not in seen_ids:
                    seen_ids.add(job["job_id"])
                    results.append(job)
        logger.info("Apple: %d matching jobs found", len(results))
        return results

    def _fetch_keyword(self, keyword: str, preferences: dict) -> List[dict]:
        params = {
            "query": keyword,
            "filters.locationList.countryAreaList.country": "USA",
            "page": 0,
            "size": 50,
        }
        headers = {"User-Agent": "Mozilla/5.0 (compatible; JobPipeline/1.0)"}
        try:
            resp = requests.get(
                self._SEARCH_URL, params=params, headers=headers, timeout=15
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Apple fetch failed for keyword '%s': %s", keyword, exc)
            return []

        data = resp.json()
        jobs_raw = data.get("searchResults", [])
        results = []
        for job in jobs_raw:
            title = job.get("postingTitle", "")
            if not _matches_title(title, preferences):
                continue
            job_id = str(job.get("positionId", ""))
            locations = job.get("locations", [])
            location_str = ", ".join(loc.get("name", "") for loc in locations) if locations else ""
            if not _matches_location(location_str, preferences):
                logger.debug("Excluded by location: %s — %s", title, location_str)
                continue
            url = job.get("jobUrl", f"https://jobs.apple.com/en-us/details/{job_id}")
            results.append({
                "job_id": job_id,
                "company": self.company_name,
                "title": title,
                "location": location_str,
                "url": url,
                "apply_url": url,
                "description": _strip_html(job.get("jobSummary", "")),
            })
        return results


class AshbyFetcher:
    """Fetches jobs from the public Ashby HQ job board API.

    API: GET https://api.ashbyhq.com/posting-api/job-board/{slug}
    No authentication required. Used by OpenAI, Cohere, and others.
    """

    _BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

    def __init__(self, board_slug: str, company_name: str):
        self.board_slug = board_slug
        self.company_name = company_name

    def fetch(self, preferences: dict) -> List[dict]:
        url = self._BASE_URL.format(slug=self.board_slug)
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Ashby fetch failed for %s: %s", self.company_name, exc)
            return []

        raw_jobs = resp.json().get("jobs", [])
        results = []
        for job in raw_jobs:
            title = job.get("title", "")
            if not _matches_title(title, preferences):
                continue
            location = job.get("location", "")
            if not location:
                addr = job.get("address", {}).get("postalAddress", {})
                parts = [addr.get("addressLocality", ""), addr.get("addressRegion", ""), addr.get("addressCountry", "")]
                location = ", ".join(p for p in parts if p)
            if not _matches_location(location, preferences):
                logger.debug("Excluded by location: %s — %s", title, location)
                continue
            published_at = job.get("publishedAt")
            date_posted = published_at[:10] if published_at else None
            results.append({
                "job_id": str(job["id"]),
                "company": self.company_name,
                "title": title,
                "location": location,
                "url": job.get("jobUrl", ""),
                "apply_url": job.get("applyUrl", job.get("jobUrl", "")),
                "description": job.get("descriptionPlain", "") or _strip_html(job.get("descriptionHtml", "")),
                "date_posted": date_posted,
            })
        logger.info("Ashby/%s: %d matching jobs found", self.company_name, len(results))
        return results


class NetflixFetcher:
    """Fetches jobs from Netflix via the Eightfold AI API.

    List:  GET https://explore.jobs.netflix.net/api/apply/v2/jobs
           ?domain=netflix.com&query=<keyword>&limit=100
    Detail: GET https://explore.jobs.netflix.net/api/apply/v2/jobs/{id}
            ?domain=netflix.com
    Description is only available on the detail endpoint.
    """

    _LIST_URL = "https://explore.jobs.netflix.net/api/apply/v2/jobs"
    _DETAIL_URL = "https://explore.jobs.netflix.net/api/apply/v2/jobs/{job_id}"

    def __init__(self, company_name: str = "Netflix"):
        self.company_name = company_name

    def fetch(self, preferences: dict) -> List[dict]:
        seen_ids: set = set()
        candidates = []
        for keyword in preferences.get("title_keywords", ["Engineering Manager"]):
            try:
                resp = requests.get(
                    self._LIST_URL,
                    params={"domain": "netflix.com", "query": keyword, "limit": 100},
                    headers={"User-Agent": "Mozilla/5.0 (compatible; JobPipeline/1.0)"},
                    timeout=15,
                )
                resp.raise_for_status()
            except Exception as exc:
                logger.warning("Netflix list fetch failed for '%s': %s", keyword, exc)
                continue
            for pos in resp.json().get("positions", []):
                job_id = str(pos.get("id", ""))
                title = pos.get("name", "")
                if job_id in seen_ids or not _matches_title(title, preferences):
                    continue
                location = pos.get("location", "")
                if not _matches_location(location, preferences):
                    logger.debug("Excluded by location: %s — %s", title, location)
                    continue
                seen_ids.add(job_id)
                candidates.append({
                    "job_id": job_id,
                    "title": title,
                    "location": location,
                    "url": pos.get("canonicalPositionUrl", f"https://jobs.netflix.com/jobs/{job_id}"),
                })

        results = []
        for c in candidates:
            try:
                import time as _time
                _time.sleep(1)  # avoid 429 rate limiting on detail fetches
                detail = requests.get(
                    self._DETAIL_URL.format(job_id=c["job_id"]),
                    params={"domain": "netflix.com"},
                    headers={"User-Agent": "Mozilla/5.0 (compatible; JobPipeline/1.0)"},
                    timeout=15,
                )
                detail.raise_for_status()
                description = _strip_html(detail.json().get("job_description", ""))
            except Exception as exc:
                logger.warning("Netflix detail fetch failed for %s: %s", c["job_id"], exc)
                description = ""
            results.append({**c, "company": self.company_name, "apply_url": c["url"], "description": description})

        logger.info("Netflix: %d matching jobs found", len(results))
        return results


class ZillowFetcher:
    """Fetches jobs from Zillow via the Workday API.

    List:  POST https://zillow.wd5.myworkdayjobs.com/wday/cxs/zillow/Zillow_Group_External/jobs
    Detail: GET https://zillow.wd5.myworkdayjobs.com/wday/cxs/zillow/Zillow_Group_External{externalPath}
    Description is only available on the detail endpoint.
    """

    _LIST_URL = "https://zillow.wd5.myworkdayjobs.com/wday/cxs/zillow/Zillow_Group_External/jobs"
    _DETAIL_BASE = "https://zillow.wd5.myworkdayjobs.com/wday/cxs/zillow/Zillow_Group_External"
    _PAGE_SIZE = 20
    _MAX_PAGES = 3  # cap at 60 results per keyword to avoid rate-limit hangs

    def __init__(self, company_name: str = "Zillow"):
        self.company_name = company_name

    def fetch(self, preferences: dict) -> List[dict]:
        seen_ids: set = set()
        candidates = []
        for keyword in preferences.get("title_keywords", ["Engineering Manager"]):
            offset = 0
            page = 0
            while page < self._MAX_PAGES:
                try:
                    resp = requests.post(
                        self._LIST_URL,
                        json={"limit": self._PAGE_SIZE, "offset": offset, "searchText": keyword},
                        headers={"User-Agent": "Mozilla/5.0 (compatible; JobPipeline/1.0)",
                                 "Content-Type": "application/json", "Accept": "application/json"},
                        timeout=8,
                    )
                    resp.raise_for_status()
                except Exception as exc:
                    logger.warning("Zillow list fetch failed for '%s': %s", keyword, exc)
                    break
                data = resp.json()
                postings = data.get("jobPostings", [])
                if not postings:
                    break
                for posting in postings:
                    title = posting.get("title", "")
                    ext_path = posting.get("externalPath", "")
                    job_id = ext_path.split("_")[-1] if "_" in ext_path else ext_path
                    if job_id in seen_ids or not _matches_title(title, preferences):
                        continue
                    location = posting.get("locationsText", "")
                    if not _matches_location(location, preferences):
                        logger.debug("Excluded by location: %s — %s", title, location)
                        continue
                    seen_ids.add(job_id)
                    candidates.append({
                        "job_id": job_id,
                        "title": title,
                        "location": location,
                        "ext_path": ext_path,
                        "url": f"https://zillow.wd5.myworkdayjobs.com/Zillow_Group_External{ext_path}",
                    })
                if len(postings) < self._PAGE_SIZE:
                    break
                offset += self._PAGE_SIZE
                page += 1

        results = []
        for c in candidates:
            try:
                detail = requests.get(
                    f"{self._DETAIL_BASE}{c['ext_path']}",
                    headers={"User-Agent": "Mozilla/5.0 (compatible; JobPipeline/1.0)",
                             "Accept": "application/json"},
                    timeout=8,
                )
                detail.raise_for_status()
                info = detail.json().get("jobPostingInfo", {})
                description = _strip_html(info.get("jobDescription", ""))
            except Exception as exc:
                logger.warning("Zillow detail fetch failed for %s: %s", c["job_id"], exc)
                description = ""
            results.append({
                "job_id": c["job_id"],
                "company": self.company_name,
                "title": c["title"],
                "location": c["location"],
                "url": c["url"],
                "apply_url": c["url"],
                "description": description,
            })

        logger.info("Zillow: %d matching jobs found", len(results))
        return results


class AmazonFetcher:
    """Fetches jobs from Amazon via the amazon.jobs REST API.

    API: GET https://www.amazon.jobs/en/search.json
         ?query=<keyword>&country_code=USA&result_limit=100&offset=<n>
    Description is included in the listing response.
    """

    _SEARCH_URL = "https://www.amazon.jobs/en/search.json"
    _PAGE_SIZE = 100

    def __init__(self, company_name: str = "Amazon"):
        self.company_name = company_name

    _MAX_RETRIES = 4
    _RETRY_BASE_DELAY = 2  # seconds; doubles each attempt

    def fetch(self, preferences: dict) -> List[dict]:
        seen_ids: set = set()
        results = []
        import random as _random
        for keyword in preferences.get("title_keywords", ["Engineering Manager"]):
            offset = 0
            while True:
                jobs = self._fetch_page(keyword, offset)
                if jobs is None:
                    break  # unrecoverable error — skip to next keyword
                if not jobs:
                    break
                for job in jobs:
                    title = job.get("title", "")
                    job_id = str(job.get("id_icims", job.get("id", "")))
                    if job_id in seen_ids or not _matches_title(title, preferences):
                        continue
                    location = job.get("location", "")
                    if not _matches_location(location, preferences):
                        logger.debug("Excluded by location: %s — %s", title, location)
                        continue
                    seen_ids.add(job_id)
                    url = f"https://www.amazon.jobs{job.get('job_path', '')}"
                    description = _strip_html(
                        job.get("description", "") or job.get("description_short", "")
                    )
                    results.append({
                        "job_id": job_id,
                        "company": self.company_name,
                        "title": title,
                        "location": location,
                        "url": url,
                        "apply_url": url,
                        "description": description,
                    })
                if len(jobs) < self._PAGE_SIZE:
                    break
                offset += self._PAGE_SIZE

        logger.info("Amazon: %d matching jobs found", len(results))
        return results

    def _fetch_page(self, keyword: str, offset: int):
        """Fetch one page with exponential backoff. Returns job list or None on failure."""
        import random as _random
        delay = self._RETRY_BASE_DELAY
        for attempt in range(self._MAX_RETRIES):
            try:
                resp = requests.get(
                    self._SEARCH_URL,
                    params={"query": keyword, "country_code": "USA",
                            "result_limit": self._PAGE_SIZE, "offset": offset},
                    headers={"User-Agent": "Mozilla/5.0 (compatible; JobPipeline/1.0)"},
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json().get("jobs", [])
            except requests.exceptions.Timeout:
                logger.warning("Amazon timeout for '%s' offset=%d (attempt %d/%d)",
                               keyword, offset, attempt + 1, self._MAX_RETRIES)
            except requests.exceptions.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 429:
                    logger.warning("Amazon rate-limited for '%s' offset=%d (attempt %d/%d)",
                                   keyword, offset, attempt + 1, self._MAX_RETRIES)
                else:
                    logger.warning("Amazon HTTP error for '%s': %s", keyword, exc)
                    return None
            except Exception as exc:
                logger.warning("Amazon fetch failed for '%s': %s", keyword, exc)
                return None
            if attempt < self._MAX_RETRIES - 1:
                jitter = _random.uniform(0, delay * 0.5)
                time.sleep(delay + jitter)
                delay *= 2
        logger.warning("Amazon: giving up on '%s' offset=%d after %d attempts",
                       keyword, offset, self._MAX_RETRIES)
        return None


class MicrosoftFetcher:
    """Fetches EM roles from Microsoft via the Eightfold REST API.

    Search:  GET https://apply.careers.microsoft.com/api/pcsx/search
                 ?domain=microsoft.com&query=<keyword>&location=United+States&start=<n>&limit=20
    Details: GET https://apply.careers.microsoft.com/api/pcsx/position_details
                 ?position_id=<id>&domain=microsoft.com&hl=en
    """

    _SEARCH_URL = "https://apply.careers.microsoft.com/api/pcsx/search"
    _DETAIL_URL = "https://apply.careers.microsoft.com/api/pcsx/position_details"
    _JOB_BASE = "https://apply.careers.microsoft.com/careers/job"
    _PAGE_SIZE = 20
    _MAX_PAGES = 10
    _HEADERS = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://jobs.careers.microsoft.com/",
    }

    def __init__(self, company_name: str = "Microsoft"):
        self.company_name = company_name

    def _fetch_description(self, job_id: str) -> tuple:
        """Returns (description_text, date_posted_iso) for a position."""
        import time
        time.sleep(1)  # avoid rate limiting
        try:
            resp = requests.get(
                self._DETAIL_URL,
                params={"position_id": job_id, "domain": "microsoft.com", "hl": "en"},
                headers=self._HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            html_desc = data.get("jobDescription", "") or ""
            text = BeautifulSoup(html_desc, "html.parser").get_text(separator="\n").strip()
            posted_ts = data.get("postedTs")
            date_posted = None
            if posted_ts:
                date_posted = datetime.fromtimestamp(posted_ts, tz=timezone.utc).strftime("%Y-%m-%d")
            return text, date_posted
        except Exception as exc:
            logger.warning("Microsoft description fetch failed for %s: %s", job_id, exc)
            return "", None

    def fetch(self, preferences: dict) -> List[dict]:
        seen_ids: set = set()
        results = []
        for keyword in preferences.get("title_keywords", ["Engineering Manager"]):
            for page in range(self._MAX_PAGES):
                start = page * self._PAGE_SIZE
                try:
                    resp = requests.get(
                        self._SEARCH_URL,
                        params={"domain": "microsoft.com", "query": keyword,
                                "location": "United States", "start": start,
                                "limit": self._PAGE_SIZE},
                        headers=self._HEADERS,
                        timeout=15,
                    )
                    resp.raise_for_status()
                except Exception as exc:
                    logger.warning("Microsoft fetch failed for '%s': %s", keyword, exc)
                    break
                positions = resp.json().get("data", {}).get("positions", [])
                if not positions:
                    break
                for pos in positions:
                    title = pos.get("name", "")
                    job_id = str(pos.get("id", ""))
                    if job_id in seen_ids or not _matches_title(title, preferences):
                        continue
                    locations = pos.get("locations") or []
                    location = locations[0] if locations else ""
                    if not _matches_location(location, preferences):
                        logger.debug("Microsoft excluded by location: %s — %s", title, location)
                        continue
                    seen_ids.add(job_id)
                    description, date_posted = self._fetch_description(job_id)
                    url = f"{self._JOB_BASE}/{job_id}"
                    results.append({
                        "job_id": job_id,
                        "company": self.company_name,
                        "title": title,
                        "location": location,
                        "url": url,
                        "apply_url": url,
                        "description": description,
                        "date_posted": date_posted,
                    })
                if len(positions) < self._PAGE_SIZE:
                    break

        logger.info("Microsoft: %d matching jobs found", len(results))
        return results


class UberFetcher:
    """Fetches EM roles from Uber via the careers search API.

    POST https://www.uber.com/api/loadSearchJobsResults?localeCode=en
    CSRF token is the static value "x" — no browser session required.
    Description is included inline in the listing response.
    """

    _API_URL = "https://www.uber.com/api/loadSearchJobsResults"

    def __init__(self, company_name: str = "Uber"):
        self.company_name = company_name

    def fetch(self, preferences: dict) -> List[dict]:
        results = []
        seen_ids: set = set()
        for keyword in preferences.get("title_keywords", ["Engineering Manager"]):
            for job in self._fetch_keyword(keyword, preferences):
                if job["job_id"] not in seen_ids:
                    seen_ids.add(job["job_id"])
                    results.append(job)
        logger.info("Uber: %d matching jobs found", len(results))
        return results

    def _fetch_keyword(self, keyword: str, preferences: dict) -> List[dict]:
        payload = {
            "limit": 100,
            "page": 0,
            "params": {
                "department": [],
                "lineOfBusinessName": [],
                "location": [],
                "programAndPlatform": [],
                "query": keyword,
            },
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; JobPipeline/1.0)",
            "Content-Type": "application/json",
            "x-csrf-token": "x",
        }
        try:
            resp = requests.post(
                self._API_URL,
                params={"localeCode": "en"},
                json=payload,
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("Uber fetch failed for '%s': %s", keyword, exc)
            return []

        if data.get("status") != "success":
            logger.warning("Uber API non-success for '%s': %s", keyword,
                           data.get("data", {}).get("message", ""))
            return []

        results = []
        for item in data.get("data", {}).get("results", []):
            title = item.get("title", "")
            if not _matches_title(title, preferences):
                continue
            loc = item.get("location", {})
            location = ", ".join(filter(None, [loc.get("city", ""), loc.get("countryName", "")]))
            if not _matches_location(location, preferences):
                logger.debug("Uber excluded by location: %s — %s", title, location)
                continue
            job_id = str(item.get("id", ""))
            job_url = f"https://www.uber.com/global/en/careers/list/{job_id}/"
            results.append({
                "job_id": job_id,
                "company": self.company_name,
                "title": title,
                "location": location,
                "url": job_url,
                "apply_url": job_url,
                "description": item.get("description", ""),
            })
        return results


class LeverFetcher:
    """Fetches jobs from Lever ATS public API.

    API: GET https://api.lever.co/v0/postings/{slug}?mode=json&limit=100
    Supports optional department filter to work around Lever's hard 100-result cap:
    large boards (e.g. Spotify) cap at 100 results with no working pagination,
    so filtering by department=Engineering surfaces EM roles that would otherwise
    be crowded out.
    Description is available inline in the listing response.
    """

    _BASE_URL = "https://api.lever.co/v0/postings/{slug}"

    def __init__(self, board_slug: str, company_name: str, department: str = None):
        self.board_slug = board_slug
        self.company_name = company_name
        self.department = department

    def fetch(self, preferences: dict) -> List[dict]:
        params = {"mode": "json", "limit": 100}
        if self.department:
            params["department"] = self.department
        try:
            resp = requests.get(
                self._BASE_URL.format(slug=self.board_slug),
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Lever fetch failed for %s: %s", self.company_name, exc)
            return []

        results = []
        for job in resp.json():
            title = job.get("text", "")
            if not _matches_title(title, preferences):
                continue
            location = job.get("categories", {}).get("location", "")
            if not _matches_location(location, preferences):
                logger.debug("Excluded by location: %s — %s", title, location)
                continue
            job_id = str(job.get("id", ""))
            job_url = job.get("hostedUrl", f"https://jobs.lever.co/{self.board_slug}/{job_id}")
            description = _strip_html(job.get("descriptionPlain", "") or job.get("description", ""))
            created_at_ms = job.get("createdAt")
            date_posted = None
            if created_at_ms:
                date_posted = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            results.append({
                "job_id": job_id,
                "company": self.company_name,
                "title": title,
                "location": location,
                "url": job_url,
                "apply_url": job.get("applyUrl", job_url),
                "description": description.strip(),
                "date_posted": date_posted,
            })
        logger.info("Lever/%s: %d matching jobs found", self.company_name, len(results))
        return results


class WalmartFetcher:
    """Fetches EM-equivalent roles from Walmart Global Tech via the careers hybrid-search API.

    Walmart uses the naming convention "Senior Manager, Software Engineering"
    instead of "Engineering Manager" — configure company-level title_keywords
    in companies.yaml to match their titles.

    Search: POST https://careers.walmart.com/api/ai/search-ai/api/v1/combined/hybrid-search
    Job description is embedded in the search response text field (no detail fetch needed).
    """

    _SEARCH_URL = (
        "https://careers.walmart.com/api/ai/search-ai/api/v1/combined/hybrid-search"
    )
    _SEARCH_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Referer": "https://careers.walmart.com/us/en/results",
        "Origin": "https://careers.walmart.com",
    }
    _PAGE_SIZE = 100
    _MAX_PAGES = 5

    def __init__(self, company_name: str = "Walmart"):
        self.company_name = company_name

    def fetch(self, preferences: dict) -> List[dict]:
        seen_ids: set = set()
        results = []
        for keyword in preferences.get("title_keywords", ["Engineering Manager"]):
            for page_num in range(self._MAX_PAGES):
                try:
                    resp = requests.post(
                        self._SEARCH_URL,
                        params={"page": page_num, "size": self._PAGE_SIZE, "locale": "en_US"},
                        json={"query": keyword, "basicSearch": False,
                              "filter": "", "locale": "en_US"},
                        headers=self._SEARCH_HEADERS,
                        timeout=20,
                    )
                    resp.raise_for_status()
                except Exception as exc:
                    logger.warning("Walmart search failed for '%s' page %d: %s", keyword, page_num, exc)
                    break

                data = resp.json()
                jobs = data.get("jobs") or []
                if not jobs:
                    break

                for job in jobs:
                    job_id = job.get("id", "")
                    if not job_id or job_id in seen_ids:
                        continue
                    meta = job.get("metadata") or {}
                    title = meta.get("title", "")
                    if not title or not _matches_title(title, preferences):
                        continue
                    city = (meta.get("primaryLocationCity") or "").title()
                    location = city
                    if not _matches_location(location, preferences):
                        logger.debug("Walmart excluded by location: %s — %s", title, location)
                        continue
                    text = job.get("text", "")
                    desc_match = re.search(r"Job Posting Description:\s*(.+)", text, re.DOTALL)
                    description = desc_match.group(1).strip() if desc_match else text
                    seen_ids.add(job_id)
                    job_url = f"https://careers.walmart.com/us/jobs/{job_id}/job"
                    results.append({
                        "job_id": job_id,
                        "company": self.company_name,
                        "title": title,
                        "location": location,
                        "url": job_url,
                        "apply_url": job_url,
                        "description": description,
                    })

                if len(jobs) < self._PAGE_SIZE:
                    break

        logger.info("Walmart: %d matching jobs found", len(results))
        return results


class LinkedInFetcher:
    """Fetches matching jobs from LinkedIn's public guest API (no auth required).

    Search: GET https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search
            ?keywords=<kw>&location=United+States&start=<n>&count=25&f_TPR=r2592000
    Detail: GET https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/<job_id>
    Both return HTML, parsed with BeautifulSoup.
    Company name is extracted from the card, not set to "LinkedIn".
    """

    _SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    _DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting"
    _PAGE_SIZE = 25
    _MAX_PAGES = 8
    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
    }

    def __init__(self, company_name: str = "LinkedIn", company_id: str = ""):
        self.company_name = company_name
        self.company_id = company_id  # LinkedIn company ID (e.g. "1337" for LinkedIn Inc.)

    def _fetch_description(self, job_id: str) -> str:
        import time
        time.sleep(1)
        try:
            resp = requests.get(
                f"{self._DETAIL_URL}/{job_id}",
                headers=self._HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            desc_el = soup.find("div", class_="show-more-less-html__markup")
            return desc_el.get_text(separator="\n").strip() if desc_el else ""
        except Exception as exc:
            logger.warning("LinkedIn description fetch failed for %s: %s", job_id, exc)
            return ""

    def fetch(self, preferences: dict) -> List[dict]:
        seen_ids: set = set()
        results = []
        for keyword in preferences.get("title_keywords", ["Engineering Manager"]):
            for page in range(self._MAX_PAGES):
                start = page * self._PAGE_SIZE
                try:
                    params = {
                        "keywords": keyword,
                        "location": "United States",
                        "start": start,
                        "count": self._PAGE_SIZE,
                        "f_TPR": "r2592000",  # last 30 days
                    }
                    if self.company_id:
                        params["f_C"] = self.company_id
                    resp = requests.get(
                        self._SEARCH_URL,
                        params=params,
                        headers=self._HEADERS,
                        timeout=15,
                    )
                    resp.raise_for_status()
                except Exception as exc:
                    logger.warning("LinkedIn search failed for '%s': %s", keyword, exc)
                    break
                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.find_all("div", class_="base-search-card")
                if not cards:
                    break
                for card in cards:
                    urn = card.get("data-entity-urn", "")
                    job_id = urn.split(":")[-1]
                    if not job_id or job_id in seen_ids:
                        continue
                    title_el = card.find("h3", class_="base-search-card__title")
                    company_el = card.find("h4", class_="base-search-card__subtitle")
                    location_el = card.find("span", class_="job-search-card__location")
                    date_el = card.find("time")
                    link_el = card.find("a", class_="base-card__full-link")
                    title = title_el.get_text(strip=True) if title_el else ""
                    company = self.company_name if self.company_id else (company_el.get_text(strip=True) if company_el else "")
                    location = location_el.get_text(strip=True) if location_el else ""
                    posted_date = date_el.get("datetime") if date_el else None
                    url = link_el["href"].split("?")[0] if link_el else ""
                    if not _matches_title(title, preferences):
                        continue
                    if not _matches_location(location, preferences):
                        logger.debug("LinkedIn excluded by location: %s — %s", title, location)
                        continue
                    seen_ids.add(job_id)
                    description = self._fetch_description(job_id)
                    results.append({
                        "job_id": job_id,
                        "company": company,
                        "title": title,
                        "location": location,
                        "url": url,
                        "apply_url": url,
                        "description": description,
                        "date_posted": posted_date,
                    })
                import time
                time.sleep(1)  # avoid rate limiting between search pages
                if len(cards) < self._PAGE_SIZE:
                    break
        logger.info("LinkedIn: %d matching jobs found", len(results))
        return results


_FETCHER_MAP = {
    "greenhouse": GreenhouseFetcher,
    "ashby": AshbyFetcher,
    "lever": LeverFetcher,
    "netflix": NetflixFetcher,
    "zillow": ZillowFetcher,
    "amazon": AmazonFetcher,
    "uber": UberFetcher,
    "microsoft": MicrosoftFetcher,
    "walmart": WalmartFetcher,
    "linkedin": LinkedInFetcher,
}

_PLAYWRIGHT_ATS = {"google", "apple", "meta"}


def fetch_all_companies(
    companies_config: List[dict],
    preferences: dict,
    log=None,
    fetch_errors: Dict[str, Any] = None,
) -> List[dict]:
    """Run all company fetchers and return aggregated job list.

    Input:
        companies_config: list of company dicts from companies.yaml
        preferences: dict from preferences.yaml
        fetch_errors: optional dict; populated with {company_name: error_msg} on failures

    Output:
        list of normalized job dicts (see module docstring)
    """
    http_companies = [c for c in companies_config if c.get("ats", "greenhouse") not in _PLAYWRIGHT_ATS]
    playwright_companies = [c for c in companies_config if c.get("ats") in _PLAYWRIGHT_ATS]

    _log = log or (lambda msg: logger.info(msg))
    all_jobs = []
    http_total = len(http_companies)

    for i, company in enumerate(http_companies, 1):
        name = company.get("name", "?")
        ats = company.get("ats", "greenhouse")
        fetcher_cls = _FETCHER_MAP.get(ats)
        if fetcher_cls is None:
            _log(f"[{i}/{http_total}] SKIP {name} — no fetcher for ATS '{ats}'")
            continue
        company_prefs = _build_company_prefs(company, preferences)
        if ats == "lever":
            fetcher = fetcher_cls(board_slug=company["board_slug"], company_name=name,
                                  department=company.get("department"))
        elif ats in ("greenhouse", "ashby"):
            fetcher = fetcher_cls(board_slug=company["board_slug"], company_name=name)
        elif ats == "linkedin":
            fetcher = fetcher_cls(company_name=name, company_id=company.get("company_id", ""))
        else:
            fetcher = fetcher_cls(company_name=name)
        fetch_timeout = company.get("fetch_timeout", 60)
        _log(f"[{i}/{http_total}] Fetching {name} ({ats})...")
        t0 = time.time()
        result_holder: Dict[str, Any] = {"jobs": [], "exc": None}

        def _fetch(f=fetcher, p=company_prefs, h=result_holder):
            try:
                h["jobs"] = f.fetch(p)
            except Exception as exc:
                h["exc"] = exc

        thread = threading.Thread(target=_fetch, daemon=True)
        thread.start()
        thread.join(timeout=fetch_timeout)
        elapsed = time.time() - t0
        if thread.is_alive():
            _log(f"  TIMEOUT after {fetch_timeout}s — skipping {name}")
            if fetch_errors is not None:
                fetch_errors[name] = f"TIMEOUT after {fetch_timeout}s"
            jobs = []
        elif result_holder["exc"]:
            _log(f"  ERROR: {result_holder['exc']}")
            if fetch_errors is not None:
                fetch_errors[name] = str(result_holder["exc"])
            jobs = []
        else:
            jobs = result_holder["jobs"]
            _log(f"  → {len(jobs)} matching jobs ({elapsed:.1f}s)")
        all_jobs.extend(jobs)

    if playwright_companies:
        from playwright.sync_api import sync_playwright
        from pipeline.playwright_fetcher import _PLAYWRIGHT_FETCHER_MAP

        _PLAYWRIGHT_DEFAULT_TIMEOUT = 1200  # 20 min hard limit per company

        with sync_playwright() as pw:
            for company in playwright_companies:
                ats = company.get("ats")
                fetcher_cls = _PLAYWRIGHT_FETCHER_MAP.get(ats)
                if fetcher_cls is None:
                    logger.warning("No Playwright fetcher for ATS '%s' (company: %s)",
                                   ats, company.get("name"))
                    continue
                company_name = company["name"]
                company_prefs = _build_company_prefs(company, preferences)
                fetcher = fetcher_cls(company_name=company_name)
                company_timeout = company.get("fetch_timeout", _PLAYWRIGHT_DEFAULT_TIMEOUT)

                # Launch a fresh browser per company so memory is freed between runs.
                browser = pw.chromium.launch(headless=True)
                page = None
                t0 = time.time()
                try:
                    page = browser.new_page(user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ))
                    timed_out_urls: List[str] = []
                    result_holder: Dict[str, Any] = {"jobs": [], "exc": None}

                    def _playwright_fetch(f=fetcher, p=company_prefs, pg=page,
                                         tu=timed_out_urls, h=result_holder):
                        try:
                            h["jobs"] = f.fetch(p, pg, log=log, timed_out_urls=tu)
                        except Exception as exc:
                            h["exc"] = exc

                    fetch_thread = threading.Thread(target=_playwright_fetch, daemon=True)
                    fetch_thread.start()
                    fetch_thread.join(timeout=company_timeout)
                    elapsed = time.time() - t0

                    if fetch_thread.is_alive():
                        msg = f"TIMEOUT after {company_timeout}s"
                        _log(f"  {msg} — skipping {company_name}")
                        if fetch_errors is not None:
                            fetch_errors[company_name] = msg
                        jobs = []
                    elif result_holder["exc"]:
                        _log(f"  ERROR fetching {company_name}: {result_holder['exc']}")
                        if fetch_errors is not None:
                            fetch_errors[company_name] = str(result_holder["exc"])
                        jobs = []
                    else:
                        jobs = result_holder["jobs"]
                        _log(f"  → {len(jobs)} matching jobs ({elapsed:.1f}s)")
                        if timed_out_urls:
                            n = len(timed_out_urls)
                            skip_msg = f"{n} description fetch(es) timed out"
                            _log(f"  WARNING: {company_name}: {skip_msg}")
                            if fetch_errors is not None:
                                fetch_errors[company_name] = skip_msg
                    all_jobs.extend(jobs)
                finally:
                    if page:
                        try:
                            page.close()
                        except Exception:
                            pass
                    try:
                        browser.close()
                    except Exception:
                        pass
                    try:
                        import resource as _resource
                        rss = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
                        import platform
                        rss_mb = rss / 1024 if platform.system() == "Darwin" else rss / 1024 / 1024
                        _log(f"[mem] After {company_name}: {rss_mb:.0f} MB RSS")
                    except Exception:
                        pass

    return all_jobs
