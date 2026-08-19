"""JSON Schema всех function tools для LLM."""

FUNCTIONS = [
    {
        "name": "create_task",
        "description": "Создать новую задачу",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Название задачи"},
                "category": {"type": "string", "enum": ["work", "personal"]},
                "priority": {"type": "string", "enum": ["high", "medium", "normal"]},
                "is_frog": {"type": "boolean", "description": "Пометить как лягушку"},
                "scheduled_date": {"type": "string", "description": "Дата, когда пользователь планирует делать задачу, YYYY-MM-DD или null"},
                "due_date": {"type": "string", "description": "Жёсткий дедлайн YYYY-MM-DD или null"},
                "due_time": {"type": "string", "description": "Время HH:MM или null"},
                "remind_at": {"type": "string", "description": "ISO datetime напоминания или null"},
                "remind_before_min": {"type": "integer", "description": "За N мин до события"},
                "repeat_rule": {
                    "type": "string",
                    "description": "Правило повторения: daily, weekdays (пн-пт), weekly:1 (пн), weekly:1,3 (пн+ср), monthly:15 (15-го числа), every:3d (каждые 3 дня), every:2w (каждые 2 недели). null если не повторяется",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_task",
        "description": "Изменить существующую задачу",
        "parameters": {
            "type": "object",
            "properties": {
                "search_query": {"type": "string", "description": "Текст для поиска задачи"},
                "updates": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "priority": {"type": "string", "enum": ["high", "medium", "normal"]},
                        "is_frog": {"type": "boolean"},
                        "scheduled_date": {"type": "string"},
                        "due_date": {"type": "string"},
                        "due_time": {"type": "string"},
                        "status": {"type": "string", "enum": ["open", "done", "cancelled"]},
                    },
                },
            },
            "required": ["search_query", "updates"],
        },
    },
    {
        "name": "complete_task",
        "description": "Отметить задачу выполненной",
        "parameters": {
            "type": "object",
            "properties": {
                "search_query": {"type": "string", "description": "Текст для поиска задачи"},
            },
            "required": ["search_query"],
        },
    },
    {
        "name": "delete_task",
        "description": "Удалить задачу (требует подтверждения)",
        "parameters": {
            "type": "object",
            "properties": {
                "search_query": {"type": "string"},
            },
            "required": ["search_query"],
        },
    },
    {
        "name": "create_note",
        "description": "Создать заметку",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["content"],
        },
    },
    {
        "name": "create_diary_entry",
        "description": "Создать запись в дневнике",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "list_tasks",
        "description": "Показать список задач. Используй когда пользователь спрашивает 'какие дела', 'что на сегодня', 'список задач', 'мои задачи', 'выполненные задачи'",
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["today", "all", "overdue", "done_today"],
                    "description": "today — задачи на сегодня, all — все открытые, overdue — просроченные, done_today — выполненные за сегодня",
                },
            },
        },
    },
    {
        "name": "search",
        "description": "Текстовый поиск по содержимому заметок, дневника, задач, мемуарника",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "scope": {"type": "string", "enum": ["all", "tasks", "notes", "diary", "memoir"]},
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_reminder",
        "description": "Создать напоминание",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "remind_at": {"type": "string", "description": "ISO datetime"},
                "repeat_rule": {
                    "type": "string",
                    "description": "Правило: daily, weekdays, weekly:1, weekly:1,3, monthly:15, every:3d, every:2w, every:1m; null если не повторяется",
                },
            },
            "required": ["message", "remind_at"],
        },
    },
    {
        "name": "create_project",
        "description": "Создать слона (крупный проект) и нарезать на бифштексы (подзадачи на 1-2 часа)",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "category": {"type": "string", "enum": ["work", "personal"]},
            },
            "required": ["title"],
        },
    },
    {
        "name": "complete_project",
        "description": "Отметить слона/крупный проект завершённым",
        "parameters": {
            "type": "object",
            "properties": {
                "search_query": {"type": "string", "description": "Текст для поиска проекта"},
            },
            "required": ["search_query"],
        },
    },
    {
        "name": "add_birthday",
        "description": "Запомнить день рождения. Используй когда пользователь говорит 'день рождения у Сергея 15 марта', 'запомни ДР Маши 5 мая', 'у мамы день рождения 22 сентября'",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Имя человека"},
                "date": {"type": "string", "description": "Дата рождения YYYY-MM-DD; если год не назван, используй 1900"},
                "year_known": {"type": "boolean", "description": "true, только если пользователь явно назвал год рождения"},
                "note": {"type": "string", "description": "Заметка (что подарить, кто это и т.д.)"},
            },
            "required": ["name", "date"],
        },
    },
    {
        "name": "get_advice",
        "description": "Дать совет по тайм-менеджменту из базы знаний Глеба Архангельского. Используй когда пользователь спрашивает 'как не прокрастинировать', 'что делать с этим слоном', 'как планировать день', 'совет по тайм-менеджменту', или любой вопрос про организацию времени",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Вопрос пользователя о тайм-менеджменте"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "respond_to_user",
        "description": "Ответить пользователю текстом (когда не нужен function call)",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
            },
            "required": ["message"],
        },
    },
]
