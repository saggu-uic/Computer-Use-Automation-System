from __future__ import annotations

import pytest_asyncio

from rote.runtime import Runtime


@pytest_asyncio.fixture(scope="session")
async def runtime():
    rt = await Runtime.create(fresh_ports=True)
    rt.announce = lambda intervention, url: None
    yield rt
    await rt.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_faults(runtime):
    await runtime.faults.clear()
    yield
    await runtime.faults.clear()
