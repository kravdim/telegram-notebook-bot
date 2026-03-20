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
                "due_date": {"type": "string", "description": "Дедлайн YYYY-MM-DD или null"},
                "due_time": {"type": "string", "description": "Время HH:MM или null"},
                "remind_at": {"type": "string", "description": "ISO datetime напоминания или null"},
                "remind_before_min": {"type": "integer", "description": "За N мин до события"},
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
                "repeat_rule": {"type": "string", "description": "RRULE или null"},
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
