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
import threading
from typing import List

from playwright.sync_api import Page

from pipeline.fetcher import _matches_title, _matches_location, _strip_html

logger = logging.getLogger(__name__)

_DESCRIPTION_TIMEOUT_S = 55


def _get_description_safe(fetch_fn, page, url, log=None, timed_out_urls=None):
    """Call fetch_fn(page, url) with a Python-level timeout.

    Playwright's own timeout doesn't fire when the browser process dies (dead
    TCP socket). This wrapper runs the fetch in a daemon thread; if it exceeds
    _DESCRIPTION_TIMEOUT_S seconds the page is closed to unblock the stuck
    goto(), and an empty string is returned so the pipeline can continue.

    log: task-drawer callable — receives a SKIPPED message on timeout.
    timed_out_urls: mutable list — the URL is appended on timeout so callers
        can report skips in fetch_errors / email.
    """
    result = [""]
    done = threading.Event()

    def _do():
        result[0] = fetch_fn(page, url)
        done.set()

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    if done.wait(timeout=_DESCRIPTION_TIMEOUT_S):
        return result[0]
    try:
        page.close()
    except Exception:
        pass
    msg = f"  SKIPPED: description fetch timed out after {_DESCRIPTION_TIMEOUT_S}s — {url}"
    if log:
        log(msg)
    logger.warning("Description fetch timed out after %ds, skipping: %s", _DESCRIPTION_TIMEOUT_S, url)
    if timed_out_urls is not None:
        timed_out_urls.append(url)
    return ""


class MetaPlaywrightFetcher:
    """Fetches EM roles from metacareers.com (React SPA)."""

    _SEARCH = "https://www.metacareers.com/jobs"

    def __init__(self, company_name: str = "Meta"):
        self.company_name = company_name

    def fetch(self, preferences: dict, page: Page, log=None, timed_out_urls=None) -> List[dict]:
        try:
            page.goto(
                f"{self._SEARCH}?q=Engineering+Manager&sort_by_new=true",
                timeout=25000,
            )
            page.wait_for_timeout(5000)
        except Exception as exc:
            logger.warning("Meta page load failed: %s", exc)
            return []

        jobs = self._extract_jobs(page, preferences, timed_out_urls=timed_out_urls)
        logger.info("Meta: %d matching jobs found", len(jobs))
        return jobs

    def _extract_jobs(self, page: Page, preferences: dict, timed_out_urls=None) -> List[dict]:
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
            desc = _get_description_safe(self._get_description, page, c["url"], timed_out_urls=timed_out_urls)
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

    def fetch(self, preferences: dict, page: Page, log=None) -> List[dict]:
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


_PLAYWRIGHT_FETCHER_MAP = {
    "meta": MetaPlaywrightFetcher,
}

PLAYWRIGHT_ATS_TYPES = set(_PLAYWRIGHT_FETCHER_MAP.keys())
