"""In-memory rate limiter for feedback submissions."""
import os
import time
from typing import Dict, Tuple
from datetime import datetime, timedelta


class RateLimiter:
    """Token bucket rate limiter tracking submissions per IP address."""

    def __init__(self, max_requests: int = 5, window_hours: int = 1):
        """Initialize rate limiter.

        Args:
            max_requests: Maximum requests per window
            window_hours: Time window in hours
        """
        self.max_requests = max_requests
        self.window_seconds = window_hours * 3600
        self.submissions: Dict[str, list] = {}  # ip -> [timestamp, timestamp, ...]
        self.cleanup_interval = 3600  # seconds
        self.last_cleanup = time.time()

    def is_allowed(self, ip_address: str) -> bool:
        """Check if an IP is allowed to make a request.

        Args:
            ip_address: IP address to check

        Returns:
            True if allowed, False if rate limited
        """
        self._cleanup_old_entries()
        now = time.time()

        if ip_address not in self.submissions:
            self.submissions[ip_address] = []

        # Remove old submissions outside the window
        cutoff = now - self.window_seconds
        self.submissions[ip_address] = [
            ts for ts in self.submissions[ip_address] if ts > cutoff
        ]

        # Check if within limit
        return len(self.submissions[ip_address]) < self.max_requests

    def record(self, ip_address: str) -> None:
        """Record a submission from an IP address.

        Args:
            ip_address: IP address making the request
        """
        if ip_address not in self.submissions:
            self.submissions[ip_address] = []
        self.submissions[ip_address].append(time.time())

    def get_remaining(self, ip_address: str) -> int:
        """Get number of remaining requests for an IP.

        Args:
            ip_address: IP address to check

        Returns:
            Number of remaining requests (0 if limited)
        """
        self._cleanup_old_entries()
        now = time.time()

        if ip_address not in self.submissions:
            return self.max_requests

        cutoff = now - self.window_seconds
        recent = [ts for ts in self.submissions[ip_address] if ts > cutoff]
        return max(0, self.max_requests - len(recent))

    def _cleanup_old_entries(self) -> None:
        """Remove old IP entries to prevent memory leak."""
        now = time.time()
        if now - self.last_cleanup < self.cleanup_interval:
            return

        # Remove IPs with no recent submissions
        cutoff = now - self.window_seconds
        for ip in list(self.submissions.keys()):
            self.submissions[ip] = [
                ts for ts in self.submissions[ip] if ts > cutoff
            ]
            if not self.submissions[ip]:
                del self.submissions[ip]

        self.last_cleanup = now


# Global rate limiter instance
_limiter: RateLimiter = None


def get_limiter(max_requests: int = None) -> RateLimiter:
    """Get or create the global rate limiter instance.

    Args:
        max_requests: Override max requests from env var or default (5)

    Returns:
        RateLimiter instance
    """
    global _limiter
    if _limiter is None:
        limit = max_requests or int(os.getenv("FEEDBACK_RATE_LIMIT", "5"))
        _limiter = RateLimiter(max_requests=limit)
    return _limiter


def get_client_ip(request) -> str:
    """Extract client IP from FastAPI request.

    Handles X-Forwarded-For header (proxies) and direct connection.

    Args:
        request: FastAPI Request object

    Returns:
        Client IP address string
    """
    # Check for X-Forwarded-For header (proxy/load balancer)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Take first IP if multiple are listed
        return forwarded.split(",")[0].strip()

    # Fall back to direct connection IP
    if request.client:
        return request.client.host

    return "unknown"
