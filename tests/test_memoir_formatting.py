from datetime import date
from types import SimpleNamespace

import pytest

import bot.scheduler.memoir as memoir_scheduler
from bot.formatters.chronometry import format_day_timeline
from bot.formatters.memoir import format_memoir_entries, format_weekly_review
from tests.fakes import FakeSessionContext


@pytest.fixture(autouse=True)
def clear_memoir_waiting_state():
    yield


def entry(day, content, value_tag="работа"):
    return SimpleNamespace(
        event_date=date(2026, 5, day),
        content=content,
        value_tag=value_tag,
    )


def test_weekly_review_keeps_full_multiline_memoir_text():
    content = (
        "Первая важная строка про переговоры и длинный контекст, который раньше резался\n"
        "Вторая строка с конкретикой, которую раньше можно было потерять\n"
        "Третья строка с выводом"
    )

    text = format_weekly_review([entry(4, content)])

    assert "длинный контекст, который раньше резался" in text
    assert "Вторая строка с конкретикой, которую раньше можно было потерять" in text
    assert "Третья строка с выводом" in text
    assert text.count("\n  ") >= 3


def test_memoir_entries_escape_html_and_keep_lines():
    content = "Строка с <важным>\nВторая & третья"

    text = format_memoir_entries([entry(5, content)])

    assert "Строка с &lt;важным&gt;" in text
    assert "Вторая &amp; третья" in text


def test_day_timeline_keeps_full_multiline_activity_text():
    import pendulum

    content = (
        "Настраивал почту для сайта\n"
        "Нашел конкретную ошибку в DNS и записал, что поменял"
    )
    entries = [
        SimpleNamespace(
            timestamp=pendulum.datetime(2026, 5, 5, 9, 30, tz="UTC"),
            category="work",
            activity_text=content,
        )
    ]

    text = format_day_timeline(entries, "Europe/Moscow")

    assert "Настраивал почту для сайта" in text
    assert "Нашел конкретную ошибку в DNS и записал, что поменял" in text


@pytest.mark.asyncio
async def test_weekly_review_splits_long_messages(monkeypatch):
    sent = []
    user = SimpleNamespace(telegram_id=42)
    entries = [entry(i, "x" * 1200) for i in range(1, 8)]

    class Bot:
        async def send_message(self, chat_id, text, parse_mode=None, **kwargs):
            sent.append((chat_id, text, parse_mode))
            return SimpleNamespace(message_id=len(sent))

    async def fake_get_entries(session, user_id, limit):
        return entries

    monkeypatch.setattr(memoir_scheduler, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(memoir_scheduler, "get_memoir_entries", fake_get_entries)

    await memoir_scheduler._send_weekly_review(Bot(), user, "Europe/Moscow")

    review_parts = sent[:-1]
    question = sent[-1]
    assert len(review_parts) > 1
    assert all(len(text) <= 4096 for _, text, _ in review_parts)
    assert "Мемуарник" in question[1]


@pytest.mark.asyncio
async def test_day_timeline_splits_long_messages(monkeypatch):
    import pendulum

    sent = []
    user = SimpleNamespace(telegram_id=42)
    entries = [
        SimpleNamespace(
            timestamp=pendulum.datetime(2026, 5, 5, 9 + i, 0, tz="UTC"),
            category="work",
            activity_text="длинная активность " + ("x" * 1200),
        )
        for i in range(5)
    ]

    class Bot:
        async def send_message(self, chat_id, text, parse_mode=None):
            sent.append((chat_id, text, parse_mode))

    async def fake_get_today_entries(session, user_id, tz):
        return entries

    monkeypatch.setattr(memoir_scheduler, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(memoir_scheduler, "get_today_entries", fake_get_today_entries)

    await memoir_scheduler._send_day_timeline(Bot(), user, "Europe/Moscow")

    assert len(sent) > 1
    assert all(len(text) <= 4096 for _, text, _ in sent)
