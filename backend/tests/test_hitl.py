import asyncio

import pytest

from app.core import hitl


@pytest.mark.asyncio
async def test_waiter_resolves():
    fut = hitl.create_waiter("a1")
    assert hitl.has_waiter("a1")

    async def resolver():
        await asyncio.sleep(0.01)
        assert hitl.resolve_waiter("a1", "approved")

    asyncio.create_task(resolver())
    assert await asyncio.wait_for(fut, timeout=1) == "approved"
    assert not hitl.has_waiter("a1")


@pytest.mark.asyncio
async def test_resolve_missing_waiter_is_false():
    assert hitl.resolve_waiter("nope", "approved") is False
