"""Shared test guards.

Any test that drives balancer_tick() into issuing a command appends to
BAL_LOG_PATH via _log_balancer_action. Most tests never patched it, so the
suite was writing synthetic entries (fake SOCs, default thresholds) into the
production logs/balancer.jsonl — found 2026-08-25 while doing forensics on a
real over-drain. Redirect it for every test; a test that wants its own path
can still monkeypatch over this.
"""

import sys

import pytest


@pytest.fixture(autouse=True)
def _isolate_balancer_log(monkeypatch, tmp_path):
    dash = sys.modules.get("dashboard")
    if dash is not None:
        monkeypatch.setattr(dash, "BAL_LOG_PATH", tmp_path / "balancer.jsonl",
                            raising=False)
