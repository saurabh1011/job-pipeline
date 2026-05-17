"""Authentication dependencies for FastAPI routes."""
import os

from fastapi import Cookie, Depends, Header, HTTPException, status

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_SERVICE_USER = {"user_id": "service", "email": "service@internal", "name": "Service", "is_admin": True}
_DEV_USER     = {"user_id": "dev",     "email": "dev@local",        "name": "Dev",     "is_admin": True}


def require_session(
    session_token: str = Cookie(default=None),
    x_api_key: str = Header(alias="x-api-key", default=None),
) -> dict:
    """Accept a session cookie (browser) or WEB_API_KEY header (automation).

    When WEB_API_KEY is not configured the server is in open-dev mode and
    every request is treated as an admin — matching the original behaviour so
    existing tests continue to pass without modification.
    """
    web_api_key = os.getenv("WEB_API_KEY", "")

    # Automated/service requests authenticated by the shared API key
    if x_api_key and web_api_key and x_api_key == web_api_key:
        return _SERVICE_USER

    # No auth configured → open dev mode
    if not web_api_key:
        return _DEV_USER

    # Browser requests authenticated by session cookie
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    from web.auth_db import validate_session
    user = validate_session(session_token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user


def require_admin(user: dict = Depends(require_session)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


# Backward-compat alias — all existing routes use this name
require_api_key = require_session
