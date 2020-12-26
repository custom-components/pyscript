"""Test pyscript user-defined decorators."""
from ast import literal_eval
import asyncio
from datetime import datetime as dt

from custom_components.pyscript.const import DOMAIN
import custom_components.pyscript.trigger as trigger
from pytest_homeassistant_custom_component.async_mock import mock_open, patch

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED
from homeassistant.setup import async_setup_component


async def setup_script(hass, notify_q, now, source):
    """Initialize and load the given pyscript."""
    scripts = [
        "/hello.py",
    ]

    with patch("custom_components.pyscript.os.path.isdir", return_value=True), patch(
        "custom_components.pyscript.glob.iglob", return_value=scripts
    ), patch("custom_components.pyscript.global_ctx.open", mock_open(read_data=source)), patch(
        "custom_components.pyscript.trigger.dt_now", return_value=now
    ), patch(
        "homeassistant.config.load_yaml_config_file", return_value={}
    ), patch(
        "custom_components.pyscript.open", mock_open(read_data=source)
    ), patch(
        "custom_components.pyscript.watchdog_start", return_value=None
    ), patch(
        "custom_components.pyscript.os.path.getmtime", return_value=1000
    ), patch(
        "custom_components.pyscript.global_ctx.os.path.getmtime", return_value=1000
    ), patch(
        "custom_components.pyscript.install_requirements", return_value=None,
    ):
        assert await async_setup_component(hass, "pyscript", {DOMAIN: {}})

    #
    # I'm not sure how to run the mock all the time, so just force the dt_now()
    # trigger function to return the given list of times in now.
    #
    def return_next_time():
        nonlocal now
        if isinstance(now, list):
            if len(now) > 1:
                return now.pop(0)
            return now[0]
        return now

    trigger.__dict__["dt_now"] = return_next_time

    if notify_q:

        async def state_changed(event):
            var_name = event.data["entity_id"]
            if var_name != "pyscript.done":
                return
            value = event.data["new_state"].state
            await notify_q.put(value)

        hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)


async def wait_until_done(notify_q):
    """Wait for the done handshake."""
    return await asyncio.wait_for(notify_q.get(), timeout=4)


async def test_decorator_errors(hass, caplog):
    """Test decorator syntax and run-time errors."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        [dt(2020, 7, 1, 11, 59, 59, 999999)],
        """
seq_num = 0

@time_trigger("startup")
def func_startup_sync(trigger_type=None, trigger_time=None):
    global seq_num

    seq_num += 1
    log.info(f"func_startup_sync setting pyscript.done = {seq_num}, trigger_type = {trigger_type}, trigger_time = {trigger_time}")
    pyscript.done = seq_num

def once(func):
    def once_func(*args, **kwargs):
        return func(*args, **kwargs)
    return once_func

def twice(func):
    def twice_func(*args, **kwargs):
        func(*args, **kwargs)
        return func(*args, **kwargs)
    return twice_func

@state_trigger("pyscript.var1 == '1'")
@once
def func1():
    global seq_num

    seq_num += 1
    pyscript.done = seq_num

@state_trigger("pyscript.var1 == '2'")
@twice
def func2():
    global seq_num

    seq_num += 1
    pyscript.done = seq_num

@state_trigger("pyscript.var1 == '3'")
@twice
@twice
@once
@once
@once
def func3():
    global seq_num

    seq_num += 1
    pyscript.done = seq_num

def repeat(num_times):
    def decorator_repeat(func):
        nonlocal num_times

        @state_trigger("pyscript.var1 == '4'")
        def wrapper_repeat(*args, **kwargs):
            for _ in range(num_times):
                value = func(*args, **kwargs)
            return value
        return wrapper_repeat
    return decorator_repeat

@repeat(3)
def func4():
    global seq_num

    seq_num += 1
    pyscript.done = seq_num

def add_state_trig(value):
    def dec_add_state_trig(func):
        nonlocal value

        @state_trigger(f"pyscript.var1 == '{value}'")
        def dec_add_state_wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return dec_add_state_wrapper
    return dec_add_state_trig


@add_state_trig(5)              # same as @state_trigger("pyscript.var1 == '5'")
@add_state_trig(7)              # same as @state_trigger("pyscript.var1 == '7'")
@state_trigger("pyscript.var1 == '9'")
def func5():
    global seq_num

    seq_num += 1
    pyscript.done = seq_num

""",
    )
    seq_num = 0

    seq_num += 1
    # fire event to start triggers, and handshake when they are running
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    assert literal_eval(await wait_until_done(notify_q)) == seq_num

    hass.states.async_set("pyscript.var1", 0)
    hass.states.async_set("pyscript.var1", 1)
    seq_num += 1
    assert literal_eval(await wait_until_done(notify_q)) == seq_num

    hass.states.async_set("pyscript.var1", 2)
    for _ in range(2):
        seq_num += 1
        assert literal_eval(await wait_until_done(notify_q)) == seq_num

    hass.states.async_set("pyscript.var1", 3)
    for _ in range(4):
        seq_num += 1
        assert literal_eval(await wait_until_done(notify_q)) == seq_num

    hass.states.async_set("pyscript.var1", 4)
    for _ in range(3):
        seq_num += 1
        assert literal_eval(await wait_until_done(notify_q)) == seq_num

    for i in range(3):
        hass.states.async_set("pyscript.var1", 5 + 2 * i)
        seq_num += 1
        assert literal_eval(await wait_until_done(notify_q)) == seq_num
