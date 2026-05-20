---
name: Test before deploying
description: Always test changes locally before pushing and deploying to remote
type: feedback
---

Always run a local test before committing and deploying. Do not assume code changes will work and push them out without verification.

**Why:** User has been repeatedly burned by changes that seem correct but fail in practice (wrong selectors, rate limiting, timeouts). Pushing untested code wastes time debugging on the remote server.

**How to apply:** For any pipeline/fetcher change, run a local smoke test (single job, single keyword, single page) before committing. Only deploy after confirming the local test passes end-to-end.
