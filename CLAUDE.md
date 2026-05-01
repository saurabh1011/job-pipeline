# Job Pipeline — Project Guidelines

## Task Logging (UI task drawer)

All background tasks run via `web/tasks.py:create_task()`. The first argument
passed to the task function is a `log` callable that writes to the task drawer
in the UI. Use it — do NOT use `logger.info()` for user-visible progress.

Signature: `log(msg: str) -> None`

**Always pass `log` through every layer** that does multi-step work:
- `fetch_all_companies(companies, prefs, log=log)`
- Playwright fetchers: `fetcher.fetch(preferences, page, log=log)`
- Any scoring or generation loop

**Required pattern for any loop over a collection:**
```python
for i, item in enumerate(items, 1):
    log(f"[{i}/{len(items)}] Stage — {item['name']}")
    t0 = time.time()
    # ... do work ...
    log(f"  → result ({time.time() - t0:.1f}s)")
```

**Required at end of every task:**
```python
log(f"\nDone. Fetched: {n_fetched}  New: {n_new}  Scored: {n_scored}  Generated: {n_generated}")
```

## Playwright companies

Google, Apple, Meta, Walmart use Playwright (browser-based). Each gets a fresh
Chromium instance so memory is freed between companies. Microsoft and Uber use
HTTP-based fetchers despite also being large companies.

## Run modes

`POST /api/pipeline/run` accepts `mode`:
- `"http"` — HTTP-only companies (~28), no browser, fast
- `"playwright"` — browser companies only (Google/Apple/Meta/Walmart)
- `null` — all companies

## State files

`jobs.db` is the source of truth (SQLite via `pipeline/store.py`). Intermediate
state for long runs is NOT currently checkpointed — a crash loses that run's
fetched jobs. Re-running will re-fetch.
