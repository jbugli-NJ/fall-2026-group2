"""Offline tests for persistent news request budgeting."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from monty_tool.news.budget import reserve_news_request


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def test_budget_persists_between_calls(tmp_path):
    state = tmp_path / "state.sqlite3"

    assert reserve_news_request(state, request_limit=2, now=NOW)
    assert reserve_news_request(state, request_limit=2, now=NOW)
    assert not reserve_news_request(state, request_limit=2, now=NOW)
    assert state.exists()


def test_reservation_expires_after_24_hours(tmp_path):
    state = tmp_path / "state.sqlite3"

    assert reserve_news_request(state, request_limit=1, now=NOW)

    assert not reserve_news_request(
        state,
        request_limit=1,
        now=NOW + timedelta(hours=24) - timedelta(seconds=1),
    )

    assert reserve_news_request(
        state,
        request_limit=1,
        now=NOW + timedelta(hours=24),
    )


def test_timezone_does_not_change_the_budget(tmp_path):
    state = tmp_path / "state.sqlite3"
    same_instant = NOW.astimezone(timezone(timedelta(hours=9)))

    assert reserve_news_request(state, request_limit=1, now=NOW)
    assert not reserve_news_request(
        state,
        request_limit=1,
        now=same_instant,
    )


def test_concurrent_reservations_respect_limit(tmp_path):
    state = tmp_path / "state.sqlite3"

    def reserve(_):
        return reserve_news_request(
            state,
            request_limit=3,
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(reserve, range(10)))

    assert sum(results) == 3


@pytest.mark.parametrize("request_limit", [0, -1, True, 1.5])
def test_invalid_limit_is_rejected_before_creating_state(
    tmp_path,
    request_limit,
):
    state = tmp_path / "state.sqlite3"

    with pytest.raises(ValueError, match="request_limit"):
        reserve_news_request(
            state,
            request_limit=request_limit,
            now=NOW,
        )

    assert not state.exists()


def test_naive_datetime_is_rejected(tmp_path):
    state = tmp_path / "state.sqlite3"

    with pytest.raises(ValueError, match="timezone"):
        reserve_news_request(
            state,
            request_limit=1,
            now=datetime(2026, 9, 29, 12, 0),
        )

    assert not state.exists()
