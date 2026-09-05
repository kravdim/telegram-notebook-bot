"""PostgreSQL consent revocation during a cloud embedding batch."""

import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete

import bot.privacy as privacy
from bot.db.engine import async_session, engine
from bot.db.models import Note, User
from bot.scheduler import reindex

pytestmark = pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1", reason="requires PostgreSQL")


@pytest.mark.asyncio
async def test_cloud_reindex_stops_remaining_batch_after_revocation(monkeypatch):
    user_id = 8_180_000_000 + int(uuid.uuid4().hex[:6], 16)
    config = SimpleNamespace(yaml_config={"embedding": {"provider": "cloud", "base_url": "https://synthetic.test/v1"}},
                             embedding_base_url="")
    monkeypatch.setattr(privacy, "settings", config)
    monkeypatch.setattr(reindex, "settings", config)
    async with async_session() as session:
        session.add(User(telegram_id=user_id, username="egress-test", cloud_processing_enabled=True,
                         privacy_notice_version=1, privacy_provider_fingerprint=privacy.provider_fingerprint()))
        await session.commit()
        session.add_all([Note(user_id=user_id, content="synthetic first"),
                         Note(user_id=user_id, content="synthetic second")])
        await session.commit()
    transmitted = []

    async def embed(text):
        transmitted.append(text)
        async with async_session() as session:
            user = await session.get(User, user_id)
            user.cloud_processing_enabled = False
            await session.commit()
        return [0.0] * 768

    monkeypatch.setattr(reindex, "_embed_client", SimpleNamespace(embed=embed))
    try:
        await reindex.reindex_missing_embeddings()
        assert len(transmitted) == 1
    finally:
        async with async_session() as session:
            await session.execute(delete(User).where(User.telegram_id == user_id))
            await session.commit()
        await engine.dispose()
