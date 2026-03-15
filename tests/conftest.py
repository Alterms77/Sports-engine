# tests/conftest.py
"""
Shared pytest configuration for Sports Engine.

Adds sports_engine/ to sys.path so all modules can be imported
without a package install step, consistent with how bot/bot.py
handles its own path setup.
"""
import sys
import os

# Ensure sports_engine/ sub-packages are importable when pytest is run
# from the repo root (the default for CI and local development).
_REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
_SPORTS_ENGINE = os.path.join(_REPO_ROOT, "sports_engine")

for _p in (_SPORTS_ENGINE, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


import pytest


@pytest.fixture(autouse=True)
def block_network_requests(monkeypatch):
    """Prevent any real HTTP requests from being made during tests."""

    def blocked_request(*args, **kwargs):
        raise ConnectionError("Network requests are blocked in tests")

    # Block requests library methods
    monkeypatch.setattr("requests.get", blocked_request)
    monkeypatch.setattr("requests.post", blocked_request)
    monkeypatch.setattr("requests.put", blocked_request)
    monkeypatch.setattr("requests.delete", blocked_request)
    monkeypatch.setattr("requests.patch", blocked_request)
    monkeypatch.setattr("requests.head", blocked_request)
    monkeypatch.setattr("requests.Session.get", blocked_request)
    monkeypatch.setattr("requests.Session.post", blocked_request)
    monkeypatch.setattr("requests.Session.put", blocked_request)
    monkeypatch.setattr("requests.Session.delete", blocked_request)
    monkeypatch.setattr("requests.Session.patch", blocked_request)
    monkeypatch.setattr("requests.Session.head", blocked_request)

    # Block urllib
    monkeypatch.setattr("urllib.request.urlopen", blocked_request)
