"""API key authentication dependency for FastAPI routes."""
import os
from fastapi import Header, HTTPException, status
from dotenv import load_dotenv

load_dotenv()

_API_KEY = os.getenv("WEB_API_KEY", "")


def require_api_key(x_api_key: str = Header(default="")) -> None:
    if not _API_KEY:
        return  # no key configured → open access (localhost dev)
    if x_api_key != _API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
