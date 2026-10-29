"""
Regression tests for the two bugs found in server.py:

1. If every candidate's collaborative_score sums to 0, the SQL's
   NULLIF(..., 0) division yields NULL for final_score, and
   round(float(row["final_score"]), 6) blows up with a TypeError
   that the `except mysql.connector.Error` clause never catches.

2. open_connection() reads required env vars with os.environ[...],
   so a missing var raises a bare KeyError instead of a ToolError.

Both are reproduced here against a mocked DB layer (no real MySQL
needed) so they can be run before/after the fix.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402


class FakeCursor:
    """Returns canned fetchone/fetchall results, one set per execute() call."""

    def __init__(self, results):
        # results: list of ("one", value) or ("all", value) tuples, in
        # the order the code under test is expected to call them.
        self._results = list(results)

    def execute(self, *_args, **_kwargs):
        pass

    def fetchone(self):
        kind, value = self._results.pop(0)
        assert kind == "one"
        return value

    def fetchall(self):
        kind, value = self._results.pop(0)
        assert kind == "all"
        return value


def make_fake_connection(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.is_connected.return_value = True
    return conn


@pytest.fixture
def db_env(monkeypatch):
    monkeypatch.setenv("DB_HOST", "localhost")
    monkeypatch.setenv("DB_PORT", "3306")
    monkeypatch.setenv("DB_NAME", "testdb")
    monkeypatch.setenv("DB_USERNAME", "u")
    monkeypatch.setenv("DB_PASSWORD", "p")


def test_zero_collaborative_score_falls_back_to_popularity_weighting(db_env, monkeypatch):
    """Bug #1 (fixed): an all-zero-similarity result no longer crashes;
    the SQL now COALESCEs the NULL division to 0 instead of propagating NULL."""
    cursor = FakeCursor(
        [
            ("one", {"affinity_count": 1}),
            (
                "all",
                [
                    {
                        "merchant_key": "M1",
                        "merchant_name": "Merchant One",
                        "merchant_category": "RETAIL",
                        "merchant_subcategory": "GENERAL",
                        "normalized_collaborative_score": 0,
                        "popularity_score": 0.5,
                        "final_score": 0.1,  # 0.80 * 0 + 0.20 * 0.5
                    }
                ],
            ),
        ]
    )
    monkeypatch.setattr(
        server.mysql.connector, "connect", lambda **_: make_fake_connection(cursor)
    )

    result = server.get_customer_recommendations(consumer_id=1, limit=5)
    assert result["recommendations"][0]["collaborativeScore"] == 0
    assert result["recommendations"][0]["score"] == pytest.approx(0.1)


def test_missing_env_var_raises_tool_error(monkeypatch):
    """Bug #2 (fixed): a missing DB_* env var is now wrapped in a ToolError
    instead of escaping as a bare KeyError."""
    for var in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USERNAME", "DB_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DB_PORT", "3306")  # has a default via os.getenv, keep others absent

    with pytest.raises(ToolError):
        server.get_customer_recommendations(consumer_id=1, limit=5)


def test_cold_start_popularity_fallback_still_works(db_env, monkeypatch):
    """Sanity check: the happy-path cold-start fallback is unaffected."""
    cursor = FakeCursor(
        [
            ("one", {"affinity_count": 0}),
            (
                "all",
                [
                    {
                        "merchant_key": "M1",
                        "merchant_name": "Merchant One",
                        "merchant_category": "RETAIL",
                        "merchant_subcategory": "GENERAL",
                        "popularity_score": 0.9,
                    }
                ],
            ),
        ]
    )
    monkeypatch.setattr(
        server.mysql.connector, "connect", lambda **_: make_fake_connection(cursor)
    )

    result = server.get_customer_recommendations(consumer_id=1, limit=5)
    assert result["coldStart"] is True
    assert result["strategy"] == "POPULARITY_FALLBACK"
    assert result["recommendations"][0]["merchantKey"] == "M1"
