"""Playwright-based job fetchers for sites that require JavaScript rendering.

Each fetcher's fetch(preferences, page) method accepts a live Playwright Page
object managed by fetch_all_companies() in fetcher.py. All fetchers return the
same normalized job dict as HTTP-based fetchers:
    {
        job_id:      str
        company:     str
        title:       str
        location:    str
        url:         str
        apply_url:   str
        description: str
    }
"""
import logging
import re
from typing import List

from playwright.sync_api import Page

from pipeline.fetcher import _matches_title, _matches_location, _strip_html

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class GooglePlaywrightFetcher:
    """Fetches EM roles from careers.google.com (React SPA)."""

    _SEARCH = "https://www.google.com/about/careers/applications/jobs/results/"

    def __init__(self, company_name: str = "Google"):
        self.company_name = company_name

    def fetch(self, preferences: dict, page: Page) -> List[dict]:
        results = []
        seen: set = set()
        for kw in preferences.get("title_keywords", ["Engineering Manager"]):
            for job in self._fetch_keyword(kw, preferences, page):
                if job["job_id"] not in seen:
                    seen.add(job["job_id"])
                    results.append(job)
        logger.info("Google: %d matching jobs found", len(results))
        return results

    _MAX_PAGES = 5
    _BASE = "https://www.google.com/about/careers/applications/"

    def _fetch_keyword(self, keyword: str, preferences: dict, page: Page) -> List[dict]:
        q = keyword.replace(' ', '+')
        candidates = []

        for page_num in range(1, self._MAX_PAGES + 1):
            url = f"{self._SEARCH}?q={q}&location=United+States"
            if page_num > 1:
                url += f"&page={page_num}"
            try:
                page.goto(url, timeout=45000)
                page.wait_for_timeout(5000)
            except Exception as exc:
                logger.warning("Google page load failed for '%s' page %d: %s", keyword, page_num, exc)
                break

            cards = page.query_selector_all(".sMn82b")
            if not cards:
                break

            page_candidates = []
            for card in cards:
                title_el = card.query_selector("h3.QJPWVe")
                if not title_el:
                    continue
                title = title_el.inner_text().strip()
                if not _matches_title(title, preferences):
                    continue

                location_el = card.query_selector("span.r0wTof")
                location = location_el.inner_text().strip() if location_el else ""
                if not _matches_location(location, preferences):
                    logger.debug("Google excluded by location: %s — %s", title, location)
                    continue

                link_el = card.query_selector("a[href*='jobs/results/']")
                href = (link_el.get_attribute("href") or "").split("?")[0] if link_el else ""
                job_id_match = re.search(r"(\d{15,})", href)
                job_id = job_id_match.group(1) if job_id_match else ""
                if not job_id:
                    continue
                job_url = self._BASE + href if href and not href.startswith("http") else href
                page_candidates.append({"job_id": job_id, "title": title,
                                        "location": location, "url": job_url})

            candidates.extend(page_candidates)
            logger.info("Google '%s' page %d: %d matching cards", keyword, page_num, len(page_candidates))

            # Stop early if this page returned fewer than 20 cards (last page)
            if len(cards) < 20:
                break

        results = []
        for c in candidates:
            desc = self._get_description(page, c["url"])
            results.append({
                "job_id": c["job_id"],
                "company": self.company_name,
                "title": c["title"],
                "location": c["location"],
                "url": c["url"],
                "apply_url": c["url"],
                "description": desc,
            })
        return results

    def _get_description(self, page: Page, url: str) -> str:
        try:
            page.goto(url, timeout=30000)
            page.wait_for_timeout(3000)
            parts = []
            for sel in [".aG5W3", ".KwJkGe", ".BDNOWe"]:
                el = page.query_selector(sel)
                if el:
                    parts.append(el.inner_text().strip())
            return "\n\n".join(parts)
        except Exception as exc:
            logger.warning("Google description fetch failed for %s: %s", url, exc)
            return ""


class ApplePlaywrightFetcher:
    """Fetches EM roles from jobs.apple.com (SSR + client hydration).

    Apple's keyword search (`q=`) returns all teams, so title filtering is
    applied client-side. Description is fetched from each detail page.
    """

    _SEARCH = "https://jobs.apple.com/en-us/search"
    _MAX_PAGES = 5

    def __init__(self, company_name: str = "Apple"):
        self.company_name = company_name

    def fetch(self, preferences: dict, page: Page) -> List[dict]:
        results = []
        seen: set = set()
        for kw in preferences.get("title_keywords", ["Engineering Manager"]):
            for job in self._fetch_keyword(kw, preferences, page):
                if job["job_id"] not in seen:
                    seen.add(job["job_id"])
                    results.append(job)
        logger.info("Apple: %d matching jobs found", len(results))
        return results

    def _fetch_keyword(self, keyword: str, preferences: dict, page: Page) -> List[dict]:
        try:
            page.goto(f"{self._SEARCH}?q={keyword.replace(' ', '+')}&sort=Latest",
                      timeout=25000)
            page.wait_for_timeout(4000)
        except Exception as exc:
            logger.warning("Apple page load failed for '%s': %s", keyword, exc)
            return []

        candidates = []
        seen_ids: set = set()

        for _ in range(self._MAX_PAGES):
            for link in page.query_selector_all("a[href*='/en-us/details/']"):
                href = link.get_attribute("href") or ""
                # Extract job ID: /en-us/details/{id}/{slug}
                id_match = re.search(r"/details/([^/]+)/", href)
                if not id_match:
                    continue
                job_id = id_match.group(1)
                if job_id in seen_ids:
                    continue

                title = link.inner_text().strip()
                if not title or not _matches_title(title, preferences):
                    continue

                # Location: look in the parent card element
                location = ""
                try:
                    card_text = page.evaluate(
                        "(el) => el.closest('li, [role=\"listitem\"], .table-col-1')?.innerText || ''",
                        link,
                    )
                    loc_match = re.search(r"Location\s*\n(.+)", card_text or "")
                    if loc_match:
                        location = loc_match.group(1).strip()
                except Exception:
                    pass

                if not _matches_location(location, preferences):
                    logger.debug("Apple excluded by location: %s — %s", title, location)
                    continue

                seen_ids.add(job_id)
                job_url = f"https://jobs.apple.com{href.split('?')[0]}"
                candidates.append({"job_id": job_id, "title": title,
                                   "location": location, "url": job_url})

            # Try to go to next page
            next_btn = page.query_selector("a[aria-label='Next Page'], button[aria-label='Next']")
            if not next_btn:
                break
            try:
                next_btn.click()
                page.wait_for_timeout(3000)
            except Exception:
                break

        results = []
        for c in candidates:
            desc = self._get_description(page, c["url"])
            results.append({
                "job_id": c["job_id"],
                "company": self.company_name,
                "title": c["title"],
                "location": c["location"],
                "url": c["url"],
                "apply_url": c["url"],
                "description": desc,
            })
        return results

    def _get_description(self, page: Page, url: str) -> str:
        try:
            page.goto(url, timeout=20000)
            page.wait_for_timeout(2000)
            el = (page.query_selector(".job-description")
                  or page.query_selector("[class*='description']")
                  or page.query_selector("main"))
            return el.inner_text().strip() if el else ""
        except Exception as exc:
            logger.warning("Apple description fetch failed for %s: %s", url, exc)
            return ""


class MetaPlaywrightFetcher:
    """Fetches EM roles from metacareers.com (React SPA)."""

    _SEARCH = "https://www.metacareers.com/jobs"

    def __init__(self, company_name: str = "Meta"):
        self.company_name = company_name

    def fetch(self, preferences: dict, page: Page) -> List[dict]:
        try:
            page.goto(
                f"{self._SEARCH}?q=Engineering+Manager&sort_by_new=true",
                timeout=25000,
            )
            page.wait_for_timeout(5000)
        except Exception as exc:
            logger.warning("Meta page load failed: %s", exc)
            return []

        jobs = self._extract_jobs(page, preferences)
        logger.info("Meta: %d matching jobs found", len(jobs))
        return jobs

    def _extract_jobs(self, page: Page, preferences: dict) -> List[dict]:
        candidates = []
        seen: set = set()

        for link in page.query_selector_all("a[href*='/profile/job_details/']"):
            href = link.get_attribute("href") or ""
            id_match = re.search(r"/job_details/(\d+)", href)
            if not id_match:
                continue
            job_id = id_match.group(1)
            if job_id in seen:
                continue

            # inner_text of the link element contains title + location + team
            text = link.inner_text().strip()
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            title = lines[0] if lines else ""
            if not title or not _matches_title(title, preferences):
                continue

            # Location is typically on the second line before "⋅"
            location = ""
            for line in lines[1:]:
                if "⋅" not in line and not any(
                    t in line for t in ("Multiple Locations", "Remote")
                ):
                    location = line
                    break
                if "Locations" in line or "Remote" in line:
                    location = line
                    break

            if not _matches_location(location, preferences):
                logger.debug("Meta excluded by location: %s — %s", title, location)
                continue

            seen.add(job_id)
            job_url = f"https://www.metacareers.com/profile/job_details/{job_id}"
            candidates.append({"job_id": job_id, "title": title,
                                "location": location, "url": job_url})

        results = []
        for c in candidates:
            desc = self._get_description(page, c["url"])
            results.append({
                "job_id": c["job_id"],
                "company": self.company_name,
                "title": c["title"],
                "location": c["location"],
                "url": c["url"],
                "apply_url": c["url"],
                "description": desc,
            })
        return results

    def _get_description(self, page: Page, url: str) -> str:
        try:
            page.goto(url, timeout=20000)
            page.wait_for_timeout(3000)
            el = (page.query_selector("[data-testid='job-description']")
                  or page.query_selector(".job-description")
                  or page.query_selector("main"))
            return el.inner_text().strip() if el else ""
        except Exception as exc:
            logger.warning("Meta description fetch failed for %s: %s", url, exc)
            return ""


class MicrosoftPlaywrightFetcher:
    """Fetches EM roles from jobs.careers.microsoft.com (Eightfold SPA)."""

    _SEARCH = "https://jobs.careers.microsoft.com/global/en/search"
    _MAX_PAGES = 5

    def __init__(self, company_name: str = "Microsoft"):
        self.company_name = company_name

    def fetch(self, preferences: dict, page: Page) -> List[dict]:
        try:
            page.goto(self._SEARCH, timeout=25000)
            page.wait_for_timeout(4000)

            # Type search query into the search box and submit
            search_input = page.wait_for_selector("#position-query-search", timeout=8000)
            search_input.fill("Engineering Manager")
            search_btn = page.wait_for_selector("button[aria-label='Search jobs']", timeout=5000)
            search_btn.click()
            page.wait_for_timeout(5000)
        except Exception as exc:
            logger.warning("Microsoft page load failed: %s", exc)
            return []

        jobs = self._extract_jobs(page, preferences)
        logger.info("Microsoft: %d matching jobs found", len(jobs))
        return jobs

    def _extract_jobs(self, page: Page, preferences: dict) -> List[dict]:
        candidates = []
        seen: set = set()

        for _ in range(self._MAX_PAGES):
            for link in page.query_selector_all("a[href*='/careers/job/']"):
                href = link.get_attribute("href") or ""
                id_match = re.search(r"/careers/job/(\d+)", href)
                if not id_match:
                    continue
                job_id = id_match.group(1)
                if job_id in seen:
                    continue

                text = link.inner_text().strip()
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                title = lines[0] if lines else ""
                if not title or not _matches_title(title, preferences):
                    continue

                location = lines[1] if len(lines) > 1 else ""
                if not _matches_location(location, preferences):
                    logger.debug("Microsoft excluded by location: %s — %s", title, location)
                    continue

                seen.add(job_id)
                job_url = f"https://jobs.careers.microsoft.com/careers/job/{job_id}"
                candidates.append({"job_id": job_id, "title": title,
                                   "location": location, "url": job_url})

            next_btn = page.query_selector(
                "button[aria-label='Next page'], [aria-label='Go to next page']"
            )
            if not next_btn:
                break
            try:
                next_btn.click()
                page.wait_for_timeout(4000)
            except Exception:
                break

        results = []
        for c in candidates:
            desc = self._get_description(page, c["url"])
            results.append({
                "job_id": c["job_id"],
                "company": self.company_name,
                "title": c["title"],
                "location": c["location"],
                "url": c["url"],
                "apply_url": c["url"],
                "description": desc,
            })
        return results

    def _get_description(self, page: Page, url: str) -> str:
        try:
            page.goto(url, timeout=20000)
            page.wait_for_timeout(3000)
            el = (page.query_selector("[class*='job-description']")
                  or page.query_selector("section[aria-label*='description' i]")
                  or page.query_selector("main"))
            return el.inner_text().strip() if el else ""
        except Exception as exc:
            logger.warning("Microsoft description fetch failed for %s: %s", url, exc)
            return ""



class WalmartPlaywrightFetcher:
    """Fetches EM roles from careers.walmart.com via in-page GraphQL API.

    Walmart's job search uses a GraphQL endpoint that requires session state.
    We navigate to the results page first, then call the API via page.evaluate().
    """

    _SEARCH_URL = "https://careers.walmart.com/results"
    _API_URL = "/api/talent/job"

    _SEARCH_QUERY = """
query JobSearch($searchInput: JobSearchInput!) {
  jobSearch(searchInput: $searchInput) {
    jobs {
      id
      title
      location
      postedDate
      jobUrl
      jobDescription
    }
    totalCount
  }
}
"""

    def __init__(self, company_name: str = "Walmart"):
        self.company_name = company_name

    def fetch(self, preferences: dict, page: Page) -> List[dict]:
        try:
            self._load_page(page)
        except Exception as exc:
            logger.warning("Walmart page load failed: %s", exc)
            return []

        results = []
        seen: set = set()
        for kw in preferences.get("title_keywords", ["Engineering Manager"]):
            for job in self._fetch_keyword(kw, preferences, page):
                if job["job_id"] not in seen:
                    seen.add(job["job_id"])
                    results.append(job)
        logger.info("Walmart: %d matching jobs found", len(results))
        return results

    def _load_page(self, page: Page) -> None:
        page.goto(f"{self._SEARCH_URL}?q=Engineering+Manager", timeout=25000)
        page.wait_for_timeout(5000)

    def _fetch_keyword(self, keyword: str, preferences: dict, page: Page) -> List[dict]:
        try:
            data = page.evaluate(
                """async (args) => {
                    const resp = await fetch(args.url, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            query: args.query,
                            variables: {
                                searchInput: {
                                    keyword: args.keyword,
                                    country: 'us',
                                    language: 'en',
                                    page: 1,
                                    pageSize: 50
                                }
                            }
                        })
                    });
                    return resp.json();
                }""",
                {"url": self._API_URL, "query": self._SEARCH_QUERY, "keyword": keyword},
            )
        except Exception as exc:
            logger.warning("Walmart API call failed for '%s': %s", keyword, exc)
            return []

        raw_jobs = []
        try:
            raw_jobs = data["data"]["jobSearch"]["jobs"]
        except (TypeError, KeyError):
            logger.warning("Walmart unexpected API response shape for '%s'", keyword)
            return []

        results = []
        for job in raw_jobs:
            title = job.get("title", "")
            if not _matches_title(title, preferences):
                continue
            location = job.get("location", "")
            if not _matches_location(location, preferences):
                logger.debug("Walmart excluded by location: %s — %s", title, location)
                continue
            job_id = str(job.get("id", ""))
            job_url = job.get("jobUrl", f"https://careers.walmart.com/us/jobs/{job_id}/job")
            results.append({
                "job_id": job_id,
                "company": self.company_name,
                "title": title,
                "location": location,
                "url": job_url,
                "apply_url": job_url,
                "description": _strip_html(job.get("jobDescription", "")),
            })
        return results


_PLAYWRIGHT_FETCHER_MAP = {
    "google": GooglePlaywrightFetcher,
    "apple": ApplePlaywrightFetcher,
    "meta": MetaPlaywrightFetcher,
    "walmart": WalmartPlaywrightFetcher,
}

PLAYWRIGHT_ATS_TYPES = set(_PLAYWRIGHT_FETCHER_MAP.keys())
