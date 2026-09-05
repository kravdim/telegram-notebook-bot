import pendulum
import pytest

from bot.db.crud import reminders as reminder_crud
from bot.db.crud.reminders import _calc_next_occurrence, next_future_occurrence
from bot.llm.client import LLMClient


def test_weekdays_repeat_skips_weekend():
    friday = pendulum.datetime(2026, 5, 1, 9, 0, tz="Europe/Moscow")
    next_dt = _calc_next_occurrence(friday, "weekdays")
    assert next_dt.date() == pendulum.date(2026, 5, 4)
    assert next_dt.hour == 9


def test_weekly_repeat_uses_iso_weekdays():
    monday = pendulum.datetime(2026, 5, 4, 9, 0, tz="Europe/Moscow")
    next_dt = _calc_next_occurrence(monday, "weekly:1,3")
    assert next_dt.date() == pendulum.date(2026, 5, 6)


def test_monthly_repeat_clamps_to_month_end():
    jan_31 = pendulum.datetime(2026, 1, 31, 10, 15, tz="Europe/Moscow")
    next_dt = _calc_next_occurrence(jan_31, "monthly:31")
    assert next_dt.date() == pendulum.date(2026, 2, 28)
    assert next_dt.hour == 10
    assert next_dt.minute == 15


def test_weekdays_midnight_uses_series_timezone_after_utc_roundtrip():
    local = pendulum.datetime(2026, 9, 4, 0, 30, tz="Europe/Moscow")
    stored = local.in_timezone("UTC")
    result = next_future_occurrence(stored.in_timezone("Europe/Moscow"), "weekdays", stored)
    assert result == pendulum.datetime(2026, 9, 7, 0, 30, tz="Europe/Moscow")


def test_recurrence_skips_overdue_backlog():
    current = pendulum.datetime(2026, 9, 1, 9, tz="Europe/Moscow")
    now = current.add(days=8, hours=1)
    assert next_future_occurrence(current, "daily", now) == current.add(days=9)


@pytest.mark.parametrize("month,day,elapsed_hours", [(3, 28, 23), (10, 24, 25)])
def test_daily_recurrence_preserves_local_hour_across_dst(month, day, elapsed_hours):
    before = pendulum.datetime(2026, month, day, 9, 15, tz="Europe/Berlin")
    following = _calc_next_occurrence(before, "daily")
    assert following.hour == 9 and following.minute == 15
    assert (following - before).total_hours() == elapsed_hours


def test_monthly_31_recovers_after_leap_february_clamp():
    before = pendulum.datetime(2028, 1, 31, 10, 15, tz="Europe/Moscow")
    february = _calc_next_occurrence(before, "monthly:31")
    march = _calc_next_occurrence(february, "monthly:31")
    assert february.day == 29 and february.month == 2
    assert march.day == 31 and march.month == 3
    assert (march.hour, march.minute) == (10, 15)


def test_llm_client_defaults_to_minimax_without_fallback():
    client = LLMClient()
    assert client.main_model == "MiniMax-M2.7"
    assert str(client.main_client.base_url) == "https://api.minimax.io/v1/"
    assert client.fallback_client is None


@pytest.mark.asyncio
async def test_mark_sent_is_idempotent_for_recurring_reminder(monkeypatch):
    class Reminder:
        id = "r1"
        is_sent = True
        repeat_rule = "daily"

    class Result:
        def scalar_one_or_none(self):
            return Reminder()

    class Session:
        added = []

        async def execute(self, _query):
            return Result()

        def add(self, value):
            self.added.append(value)

        async def commit(self):
            raise AssertionError("already sent reminder must not be committed again")

    session = Session()
    await reminder_crud.mark_sent(session, "r1")
    assert session.added == []
