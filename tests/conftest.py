"""Shared test fixtures."""
import pytest
from unittest.mock import MagicMock
from pipeline.llm import LLMProvider


@pytest.fixture
def mock_provider():
    """A mock LLMProvider that returns configurable responses."""
    provider = MagicMock(spec=LLMProvider)
    provider.name = "mock"
    provider.complete.return_value = "mock complete response"
    provider.complete_fast.return_value = "mock fast response"
    return provider
