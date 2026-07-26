"""Tests for config settings.

These are synchronous unit tests for pydantic-settings defaults.
No database or async fixtures are needed.
"""

from app.core.config import get_settings


def test_search_radius_km_default():
    """T27: search_radius_km defaults to 25."""
    settings = get_settings()
    assert settings.search_radius_km == 25.0


def test_search_radius_m_computed():
    """T28: search_radius_m = search_radius_km * 1000."""
    settings = get_settings()
    assert settings.search_radius_m == 25000.0
