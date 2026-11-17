"""Test trigger decorators on class methods (per-instance registration)."""

from ast import literal_eval
import asyncio
from datetime import datetime as dt
from unittest.mock import mock_open, patch

import pytest

from custom_components.pyscript import trigger
from custom_components.pyscript.const import DOMAIN
from custom_components.pyscript.function import Function
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED
from homeassistant.setup import async_setup_component


async def setup_script(hass, notify_q, now, source):
    """Initialize and load the given pyscript."""
    scripts = ["/some/config/dir/pyscripts/hello.py"]

    Function.hass = None

    with (
        patch("custom_components.pyscript.os.path.isdir", return_value=True),
        patch("custom_components.pyscript.glob.iglob", return_value=scripts),
        patch("custom_components.pyscript.global_ctx.open", mock_open(read_data=source)),
        patch("custom_components.pyscript.open", mock_open(read_data=source)),
        patch("custom_components.pyscript.trigger.dt_now", return_value=now),
        patch("homeassistant.config.load_yaml_config_file", return_value={}),
        patch("custom_components.pyscript.watchdog_start", return_value=None),
        patch("custom_components.pyscript.os.path.getmtime", return_value=1000),
        patch("custom_components.pyscript.global_ctx.os.path.getmtime", return_value=1000),
        patch("custom_components.pyscript.install_requirements", return_value=None),
    ):
        assert await async_setup_component(hass, "pyscript", {DOMAIN: {}})

    trigger.__dict__["dt_now"] = lambda: now

    if notify_q:

        async def state_changed(event):
            if event.data["entity_id"] != "pyscript.done":
                return
            await notify_q.put(event.data["new_state"].state)

        hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)


async def wait_until_done(notify_q):
    """Wait for the done handshake."""
    return await asyncio.wait_for(notify_q.get(), timeout=4)


@pytest.mark.asyncio
async def test_method_trigger_self_bound(hass):
    """A @state_trigger method fires with the right self and keeps per-instance state."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
class Counter:
    def __init__(self, name):
        self.name = name
        self.count = 0

    @state_trigger("pyscript.avar1 == '1'")
    def bump(self, var_name=None, value=None):
        self.count = self.count + 1
        pyscript.done = [self.name, self.count]

c = Counter("c1")
""",
    )

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    hass.states.async_set("pyscript.avar1", "1")
    assert literal_eval(await wait_until_done(notify_q)) == ["c1", 1]

    hass.states.async_set("pyscript.avar1", "0")
    hass.states.async_set("pyscript.avar1", "1")
    assert literal_eval(await wait_until_done(notify_q)) == ["c1", 2]


@pytest.mark.asyncio
async def test_method_trigger_two_instances(hass):
    """Two instances each get their own trigger and fire independently."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
class Counter:
    def __init__(self, name):
        self.name = name

    @state_trigger("pyscript.bvar1 == '1'")
    def bump(self, var_name=None, value=None):
        pyscript.done = self.name

c1 = Counter("c1")
c2 = Counter("c2")
""",
    )

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    hass.states.async_set("pyscript.bvar1", "1")
    got = {await wait_until_done(notify_q) for _ in range(2)}
    assert got == {"c1", "c2"}


@pytest.mark.asyncio
async def test_method_trigger_death_stops(hass):
    """When the instance is collected, its trigger stops firing."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
class Counter:
    def __init__(self, name):
        self.name = name

    @state_trigger("pyscript.cvar1 == '1'")
    def bump(self, var_name=None, value=None):
        pyscript.done = self.name

c = Counter("alive")

@service
def kill():
    global c
    del c
    pyscript.done = "killed"
""",
    )

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    hass.states.async_set("pyscript.cvar1", "1")
    assert await wait_until_done(notify_q) == "alive"

    await hass.services.async_call("pyscript", "kill", blocking=True)
    assert await wait_until_done(notify_q) == "killed"

    # let the instance's finalize stop the trigger
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()

    hass.states.async_set("pyscript.cvar1", "0")
    hass.states.async_set("pyscript.cvar1", "1")
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(notify_q.get(), timeout=1)


@pytest.mark.asyncio
async def test_selfless_method_trigger_unchanged(hass):
    """A trigger method without self keeps the old class-level (static) behavior."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
class Helper:
    @state_trigger("pyscript.dvar1 == '1'")
    def static_thing(var_name=None, value=None):
        pyscript.done = "static"
""",
    )

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    hass.states.async_set("pyscript.dvar1", "1")
    assert await wait_until_done(notify_q) == "static"


@pytest.mark.asyncio
async def test_self_in_condition(hass):
    """A trigger condition can reference self.<attr>; only matching instances fire."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
class Thing:
    def __init__(self, target):
        self.target = target

    @state_trigger("pyscript.evar1 == self.target")
    def fire(self, var_name=None, value=None):
        pyscript.done = self.target

a = Thing("hello")
b = Thing("world")
""",
    )

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    hass.states.async_set("pyscript.evar1", "world")
    assert await wait_until_done(notify_q) == "world"

    hass.states.async_set("pyscript.evar1", "nope")
    hass.states.async_set("pyscript.evar1", "hello")
    assert await wait_until_done(notify_q) == "hello"


@pytest.mark.asyncio
async def test_trigger_method_override_undecorated(hass):
    """An undecorated override replaces the inherited trigger method."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
class Base:
    @state_trigger("pyscript.fvar1 == '1'")
    def handler(self, var_name=None, value=None):
        pyscript.done = "base"

class Sub(Base):
    def handler(self, var_name=None, value=None):
        pyscript.done = "sub"

s = Sub()
""",
    )

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    hass.states.async_set("pyscript.fvar1", "1")
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(notify_q.get(), timeout=1)


@pytest.mark.asyncio
async def test_trigger_method_override_decorated(hass):
    """A decorated override replaces the inherited trigger method, firing once."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
class Base:
    @state_trigger("pyscript.gvar1 == '1'")
    def handler(self, var_name=None, value=None):
        pyscript.done = "base"

class Sub(Base):
    @state_trigger("pyscript.gvar1 == '1'")
    def handler(self, var_name=None, value=None):
        pyscript.done = "sub"

s = Sub()
""",
    )

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    hass.states.async_set("pyscript.gvar1", "1")
    assert await wait_until_done(notify_q) == "sub"
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(notify_q.get(), timeout=1)


@pytest.mark.asyncio
async def test_trigger_method_inherited(hass):
    """A subclass without an override gets the inherited trigger method."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
class Base:
    @state_trigger("pyscript.hvar1 == '1'")
    def handler(self, var_name=None, value=None):
        pyscript.done = "inherited"

class Sub(Base):
    def __init__(self, tag):
        self.tag = tag

s = Sub("sub-instance")
""",
    )

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    hass.states.async_set("pyscript.hvar1", "1")
    assert await wait_until_done(notify_q) == "inherited"
