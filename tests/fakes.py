from types import SimpleNamespace


class FakeBot:
    def __init__(self):
        self.actions = []

    async def send_chat_action(self, chat_id, action):
        self.actions.append((chat_id, action))


class FakeMessage:
    def __init__(self, text="", user_id=1, reply_to_message=None):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=user_id)
        self.bot = FakeBot()
        self.reply_to_message = reply_to_message
        self.answers = []
        self.edits = []
        self.message_id = 999

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return SimpleNamespace(message_id=self.message_id + len(self.answers))

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class FakeCallback:
    def __init__(self, user_id=1, message=None, data=None):
        self.from_user = SimpleNamespace(id=user_id)
        self.message = message or FakeMessage(user_id=user_id)
        self.data = data
        self.answered = []

    async def answer(self, text=None, **kwargs):
        self.answered.append((text, kwargs))


class FakeSessionContext:
    def __init__(self, session=None):
        self.session = session or FakeSession()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def flush(self):
        return None

    async def refresh(self, value):
        return None
