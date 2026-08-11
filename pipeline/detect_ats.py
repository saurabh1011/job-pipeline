"""ATS detection: given a company name, identify which ATS they use and what their board slug is.

Uses concurrent HTTP requests to test Greenhouse, Ashby, and Lever APIs.
Known custom-ATS companies (Google, Apple, Meta, etc.) are returned immediately.
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# Companies with custom ATS implementations — matched against lowercase company name.
_KNOWN_CUSTOM: dict = {
    "google": "google",
    "apple": "apple",
    "meta": "meta",
    "facebook": "meta",
    "microsoft": "microsoft",
    "uber": "uber",
    "walmart": "walmart",
    "netflix": "netflix",
    "zillow": "zillow",
    "amazon": "amazon",
    "linkedin": "linkedin",
    "paypal": "paypal",
    "github": "github",
    "rippling": "rippling",
}

_TIMEOUT = 8


def _slug_candidates(company_name: str) -> list:
    """Generate likely ATS board slug candidates from a company name.

    Input:  company name string (e.g. "DoorDash USA")
    Output: list of slug strings in priority order, deduplicated
    """
    name = company_name.strip()
    slug_plain = re.sub(r"[^a-z0-9]", "", name.lower())
    slug_hyph  = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    seen = set()
    candidates = []
    for s in [slug_plain, slug_hyph] + [slug_plain + suffix for suffix in ("usa", "inc", "ai", "hq")]:
        if s and s not in seen:
            seen.add(s)
            candidates.append(s)
    return candidates


def _test_greenhouse(slug: str) -> bool:
    try:
        r = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            params={"content": "false"},
            timeout=_TIMEOUT,
        )
        return r.status_code == 200 and "jobs" in r.json()
    except Exception:
        return False


def _test_ashby(slug: str) -> bool:
    try:
        r = requests.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            timeout=_TIMEOUT,
        )
        return r.status_code == 200
    except Exception:
        return False


def _test_lever(slug: str) -> bool:
    try:
        r = requests.get(
            f"https://api.lever.co/v0/postings/{slug}",
            params={"mode": "json", "limit": 1},
            timeout=_TIMEOUT,
        )
        return r.status_code == 200 and isinstance(r.json(), list)
    except Exception:
        return False


_ATS_TESTERS = [
    ("greenhouse", _test_greenhouse),
    ("ashby",      _test_ashby),
    ("lever",      _test_lever),
]


def detect_ats(company_name: str) -> dict:
    """Detect ATS platform for a company name.

    Input:  company_name str (e.g. "CoreWeave")
    Output: {
        ats:        str | None,   # greenhouse | ashby | lever | google | apple | ...
        board_slug: str | None,   # None for known custom-ATS companies
        tried:      list[str],    # "ats:slug" pairs tested (for diagnostics)
        error:      str | None
    }
    """
    name_lower = company_name.strip().lower()
    if name_lower in _KNOWN_CUSTOM:
        return {"ats": _KNOWN_CUSTOM[name_lower], "board_slug": None, "tried": [], "error": None}

    slugs = _slug_candidates(company_name)
    tasks = [(ats, slug, fn) for ats, fn in _ATS_TESTERS for slug in slugs]

    results: dict = {}
    tried: list = []
    with ThreadPoolExecutor(max_workers=min(len(tasks), 15)) as executor:
        future_map = {executor.submit(fn, slug): (ats, slug) for ats, slug, fn in tasks}
        for future in as_completed(future_map):
            ats, slug = future_map[future]
            tried.append(f"{ats}:{slug}")
            try:
                results[(ats, slug)] = future.result()
            except Exception:
                results[(ats, slug)] = False

    # Return in priority order: greenhouse > ashby > lever, primary slug first
    for ats, _ in _ATS_TESTERS:
        for slug in slugs:
            if results.get((ats, slug)):
                return {"ats": ats, "board_slug": slug, "tried": tried, "error": None}

    summary = ", ".join(tried[:6]) + ("…" if len(tried) > 6 else "")
    return {
        "ats": None,
        "board_slug": None,
        "tried": tried,
        "error": f"Could not detect ATS for '{company_name}'. Tried: {summary}",
    }
