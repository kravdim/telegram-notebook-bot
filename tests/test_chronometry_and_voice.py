from bot.handlers.chronometry import _chrono_pause_minutes, _is_plain_reaction, _sanitize_reaction
from bot.handlers.voice import _awaiting_edit, _pending_transcripts, consume_voice_edit


def test_chronometry_pause_minutes_for_common_activities():
    assert _chrono_pause_minutes("Обедаю", "rest") == 30
    assert _chrono_pause_minutes("Разговаривал по телефону с Андреем", "work") == 20
    assert _chrono_pause_minutes("воюю с почтой", "work") == 15
    assert _chrono_pause_minutes("пишу код", "work") == 0


def test_plain_reaction_filter_limits_boltliness():
    assert _is_plain_reaction("Записал: работа.") is True
    assert _is_plain_reaction("Что именно делал? Как давно? Почему так?") is False
    assert _is_plain_reaction("x" * 181) is False


def test_chronometry_reaction_sanitizes_unknown_results():
    assert (
        _sanitize_reaction(
            "Планировали с коллегами командировку в Вологду",
            "Хорошо, командировку оформили.",
        )
        == "Планирование командировки записал."
    )
    assert _sanitize_reaction("Отвез ФФ", "Хорошо, значит ФФ отправлен.") == "Записал."


def test_voice_edit_state_is_consumed_once():
    user_id = 777
    _awaiting_edit.add(user_id)
    _pending_transcripts[user_id] = "старый текст"

    assert consume_voice_edit(user_id) is True
    assert user_id not in _awaiting_edit
    assert user_id not in _pending_transcripts
    assert consume_voice_edit(user_id) is False
