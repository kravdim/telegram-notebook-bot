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

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class FakeCallback:
    def __init__(self, user_id=1, message=None):
        self.from_user = SimpleNamespace(id=user_id)
        self.message = message or FakeMessage(user_id=user_id)
        self.answered = []

    async def answer(self, text=None, **kwargs):
        self.answered.append((text, kwargs))


class FakeSessionContext:
    def __init__(self, session=None):
        self.session = session or object()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False
