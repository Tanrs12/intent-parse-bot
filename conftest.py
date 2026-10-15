"""Shared pytest setup: keep the engine cache from leaking between tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_engine_cache():
    from draftcoach import engine_cache

    engine_cache.uninstall()
    yield
    engine_cache.uninstall()
