import pendulum

from bot.db.crud.reminders import _calc_next_occurrence
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


def test_llm_client_defaults_to_minimax_without_fallback():
    client = LLMClient()
    assert client.main_model == "MiniMax-M2.7"
    assert str(client.main_client.base_url) == "https://api.minimax.io/v1/"
    assert client.fallback_client is None
