# Этап 2. LLM-клиент с fallback

## Цель
Бот понимает свободный текст через LLM, выполняет function calls, переключается на fallback при сбое.

## Задачи

### 2.1 LLMClient (llm/client.py)
```python
class LLMClient:
    """Единый клиент для работы с LLM API.
    Использует OpenAI Python SDK с переключаемым base_url.
    """
    def __init__(self, config):
        self.main_client = AsyncOpenAI(base_url=config.llm.main.base_url, api_key=...)
        self.fallback_client = AsyncOpenAI(base_url=config.llm.fallback.base_url, api_key=...)
        self.active = "main"

    async def chat(self, messages, functions=None, timeout=None) -> LLMResponse:
        """Отправить запрос. При ошибке main — автоматический retry на fallback."""
        # 1. Попытка main
        # 2. При ошибке (timeout, 5xx, rate limit) — переключение на fallback
        # 3. Логирование в llm_logs
        # 4. Если оба упали — raise LLMUnavailableError
```

Ключевые моменты:
- Таймаут из config (intent_detection: 3 сек, декомпозиция: 15 сек, суммаризация: 20 сек)
- Каждый запрос логируется в llm_logs (prompt_key, model, tokens, latency, error)
- Health check main каждые 5 минут через scheduler. При восстановлении — self.active = "main"

### 2.2 Очередь с приоритетами (llm/queue.py)
```python
class LLMQueue:
    """asyncio.PriorityQueue для LLM-запросов.
    Приоритеты: 1=напоминания, 2=intent, 3=хронометраж, 4=декомпозиция, 5=суммаризация
    """
```
- Один воркер обрабатывает очередь последовательно
- При полной очереди — пользователю typing + ожидание

### 2.3 Function Calling (llm/functions.py + llm/dispatcher.py)
- functions.py: JSON Schema всех функций (из CLAUDE.md)
- dispatcher.py: получает function_call от LLM → валидирует → исполняет CRUD → возвращает результат
- Валидация перед записью в БД:
  - due_date не в прошлом для новых задач
  - title не пустой, <= 500 символов
  - datetime парсится через pendulum с timezone пользователя
  - category, priority из допустимого enum
- Библиотека `json_repair` для автокоррекции невалидного JSON
- При невалидных данных — clarify: переспросить конкретный параметр

### 2.4 Промпты в БД (llm/prompts.py)
- Загрузка активного промпта по prompt_key из prompt_versions
- Кэширование в памяти с TTL 5 минут
- Начальное заполнение: INSERT промптов для intent_detection, chronometry_reaction, memoir_value_extraction, morning_digest, evening_summary, context_compression, decompose_project (в Alembic миграции)

### 2.5 Обработчик свободного текста (handlers/messages.py)
Основной flow:
1. Пользователь пишет текст
2. Показать typing
3. Загрузить промпт intent_detection
4. Отправить в LLM с functions
5. Получить function_call или respond_to_user
6. Если function_call → dispatcher → валидация → CRUD → подтверждение пользователю
7. Если respond_to_user → отправить текст

### 2.6 Компрессия контекста (llm/client.py)
- Хранить историю сессии в памяти (dict: user_id → list[messages])
- При >3000 токенов — суммаризировать старые через LLM (prompt_key: context_compression)
- Резюме как первое system-сообщение
- Свежие 5 пар — полностью
- При рестарте бота — история очищается

### 2.7 Проверка
- [ ] Бот понимает "Напомни завтра в 14:00 позвонить Ивану" → создаёт задачу + напоминание
- [ ] Бот понимает "Запиши заметку: ..." → создаёт заметку
- [ ] При отключении DeepSeek — автоматическое переключение на MiniMax
- [ ] Невалидный JSON от LLM — json_repair исправляет или бот переспрашивает
- [ ] Все LLM-запросы логируются в llm_logs
- [ ] /prompts показывает список промптов (admin)
