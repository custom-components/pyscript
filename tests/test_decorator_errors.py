"""Test pyscript decorator syntax error and eval-time exception reporting."""
from ast import literal_eval
import asyncio
from datetime import datetime as dt

from custom_components.pyscript.const import DOMAIN
from custom_components.pyscript.function import Function
import custom_components.pyscript.trigger as trigger
from pytest_homeassistant_custom_component.async_mock import mock_open, patch

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED
from homeassistant.setup import async_setup_component


async def setup_script(hass, notify_q, now, source):
    """Initialize and load the given pyscript."""
    scripts = [
        "/hello.py",
    ]

    Function.hass = None

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
        [dt(2020, 7, 1, 10, 59, 59, 999999), dt(2020, 7, 1, 11, 59, 59, 999999)],
        """
seq_num = 0

@time_trigger("startup")
def func_startup_sync(trigger_type=None, trigger_time=None):
    global seq_num

    seq_num += 1
    log.info(f"func_startup_sync setting pyscript.done = {seq_num}, trigger_type = {trigger_type}, trigger_time = {trigger_time}")
    pyscript.done = seq_num

@state_trigger("z + ")
def func1():
    pass

@event_trigger("some_event", "func(")
def func2():
    pass

@state_trigger("True")
@state_active("z = 1")
def func3():
    pass

@state_trigger("1 / int(pyscript.var1)")
def func5():
    pass

@state_trigger("False", ["False", "False", "pyscript.var1"])
@state_active("1 / pyscript.var1")
def func6():
    pass

@state_trigger("False", "False", ["pyscript.var7"])
def func7():
    global seq_num

    try:
        task.wait_until(state_trigger="z +")
    except SyntaxError as exc:
        log.error(exc)

    try:
        task.wait_until(event_trigger=["event", "z+"])
    except SyntaxError as exc:
        log.error(exc)

    try:
        task.wait_until(state_trigger="pyscript.var1 + 1")
    except TypeError as exc:
        log.error(exc)

    seq_num += 1
    pyscript.done = seq_num

@state_trigger("pyscript.var_done")
def func_wrapup():
    global seq_num

    seq_num += 1
    pyscript.done = seq_num

@state_trigger("z")
def func8():
    pass

""",
    )
    seq_num = 0

    seq_num += 1
    # fire event to start triggers, and handshake when they are running
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    assert literal_eval(await wait_until_done(notify_q)) == seq_num

    hass.states.async_set("pyscript.var1", 1)
    hass.states.async_set("pyscript.var1", 0)

    seq_num += 1
    hass.states.async_set("pyscript.var7", 1)
    assert literal_eval(await wait_until_done(notify_q)) == seq_num

    seq_num += 1
    hass.states.async_set("pyscript.var_done", 1)
    assert literal_eval(await wait_until_done(notify_q)) == seq_num

    assert (
        "SyntaxError: unexpected EOF while parsing (file.hello.func1 @state_trigger(), line 1)"
        in caplog.text
    )
    assert (
        "SyntaxError: unexpected EOF while parsing (file.hello.func2 @event_trigger(), line 1)"
        in caplog.text
    )
    assert "SyntaxError: invalid syntax (file.hello.func3 @state_active(), line 1)" in caplog.text
    assert (
        "trigger file.hello.func8: @state_trigger is not watching any variables; will never trigger"
        in caplog.text
    )
    assert (
        """Exception in <file.hello.func5 @state_trigger()> line 1:
    1 / int(pyscript.var1)
            ^
ZeroDivisionError: division by zero"""
        in caplog.text
    )

    assert (
        """Exception in <file.hello.func6 @state_active()> line 1:
    1 / pyscript.var1
        ^
TypeError: unsupported operand type(s) for /: 'int' and 'StateVal'"""
        in caplog.text
    )

    assert "unexpected EOF while parsing (file.hello.func7 state_trigger, line 1)" in caplog.text
    assert 'can only concatenate str (not "int") to str' in caplog.text


async def test_decorator_errors_missing_trigger(hass, caplog):
    """Test decorator syntax and run-time errors."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        [dt(2020, 7, 1, 10, 59, 59, 999999)],
        """
@state_active("z + ")
def func4():
    pass
""",
    )
    assert (
        "func4 defined in file.hello: needs at least one trigger decorator (ie: event_trigger, mqtt_trigger, state_trigger, time_trigger)"
        in caplog.text
    )


async def test_decorator_errors_missing_arg(hass, caplog):
    """Test decorator syntax and run-time errors."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        [dt(2020, 7, 1, 10, 59, 59, 999999)],
        """
@state_trigger
def func8():
    pass
""",
    )
    assert (
        "TypeError: function 'func8' defined in file.hello: decorator @state_trigger needs at least one argument"
        in caplog.text
    )


async def test_decorator_errors_missing_arg2(hass, caplog):
    """Test decorator syntax and run-time errors."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        [dt(2020, 7, 1, 10, 59, 59, 999999)],
        """
@event_trigger
def func9():
    pass
""",
    )
    assert (
        "TypeError: function 'func9' defined in file.hello: decorator @event_trigger needs at least one argument"
        in caplog.text
    )


async def test_decorator_errors_bad_arg_type(hass, caplog):
    """Test decorator syntax and run-time errors."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        [dt(2020, 7, 1, 10, 59, 59, 999999)],
        """
@state_trigger([None])
def func10():
    pass
""",
    )
    assert (
        "TypeError: function 'func10' defined in file.hello: decorator @state_trigger argument 1 should be a string, or list, or set"
        in caplog.text
    )


async def test_decorator_errors_bad_arg_type2(hass, caplog):
    """Test decorator syntax and run-time errors."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        [dt(2020, 7, 1, 10, 59, 59, 999999)],
        """
@state_trigger(False)
def func11():
    pass
""",
    )
    assert (
        "TypeError: function 'func11' defined in file.hello: decorator @state_trigger argument 1 should be a string"
        in caplog.text
    )


async def test_service_reload_error(hass, caplog):
    """Test using a reserved name generates an error."""

    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@service
def reload():
    pass
""",
    )
    assert (
        "SyntaxError: function 'reload' defined in file.hello: @service conflicts with builtin service"
        in caplog.text
    )


async def test_service_state_active_extra_args(hass, caplog):
    """Test using extra args to state_active generates an error."""

    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@state_active("arg1", "too many args")
def func4():
    pass
""",
    )
    assert (
        "TypeError: function 'func4' defined in file.hello: decorator @state_active got 2 arguments, expected 1"
        in caplog.text
    )


async def test_service_wrong_arg_type(hass, caplog):
    """Test using too many args with service an error."""

    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@service(1)
def func5():
    pass
""",
    )
    assert (
        "TypeError: function 'func5' defined in file.hello: decorator @service argument 1 should be a string"
        in caplog.text
    )


async def test_time_trigger_wrong_arg_type(hass, caplog):
    """Test using wrong argument type generates an error."""

    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@time_trigger("wrong arg type", 50)
def func6():
    pass
""",
    )
    assert (
        "TypeError: function 'func6' defined in file.hello: decorator @time_trigger argument 2 should be a string"
        in caplog.text
    )


async def test_decorator_kwargs(hass, caplog):
    """Test invalid keyword arguments generates an error."""

    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@time_trigger("invalid kwargs", arg=10)
def func7():
    pass
""",
    )
    assert (
        "TypeError: function 'func7' defined in file.hello: decorator @time_trigger invalid keyword argument 'arg'"
        in caplog.text
    )


async def test_decorator_kwargs2(hass, caplog):
    """Test invalid keyword arguments generates an error."""

    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@task_unique("invalid kwargs", arg=10)
def func7():
    pass
""",
    )
    assert (
        "TypeError: function 'func7' defined in file.hello: decorator @task_unique invalid keyword argument 'arg'"
        in caplog.text
    )


async def test_decorator_kwargs3(hass, caplog):
    """Test invalid keyword arguments type generates an error."""

    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@state_trigger("abc.xyz", kwargs=10)
def func7():
    pass
""",
    )
    assert (
        "TypeError: function 'func7' defined in file.hello: decorator @state_trigger keyword 'kwargs' should be type dict"
        in caplog.text
    )


async def test_decorator_kwargs4(hass, caplog):
    """Test invalid keyword arguments type generates an error."""

    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@state_trigger("abc.xyz", watch=10)
def func7():
    pass
""",
    )
    assert (
        "TypeError: function 'func7' defined in file.hello: decorator @state_trigger keyword 'watch' should be type list or set"
        in caplog.text
    )
