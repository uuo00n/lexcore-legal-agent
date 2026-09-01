"""Shared deterministic fixtures for unit and integration tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def legal_scenarios() -> dict:
    fixture_path = Path(__file__).parent / "fixtures" / "legal_scenarios.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


@pytest.fixture
def grounded_law(legal_scenarios: dict) -> dict:
    return dict(legal_scenarios["law"])
