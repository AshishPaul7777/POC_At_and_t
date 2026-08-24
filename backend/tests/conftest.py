"""Shared test setup.

``app.db.session.engine`` is a module-level pooled engine, so its connections
belong to whichever event loop first used them. pytest-asyncio gives each test a
fresh loop, so a pooled connection carried across tests is bound to a loop that
has already closed -- surfacing as a confusing "Event loop is closed" from deep
inside asyncpg rather than as anything to do with the test.

Disposing the pool after every test costs a reconnect and removes the whole
class of failure.
"""

from __future__ import annotations

import pytest

from app.db.session import engine


@pytest.fixture(autouse=True)
async def _dispose_engine_between_tests():
    yield
    await engine.dispose()
