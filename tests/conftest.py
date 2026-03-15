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
