"""Test pyscript @service decorator."""

import asyncio
import pathlib
from unittest.mock import patch

import pytest

from custom_components.pyscript.const import DOMAIN
from homeassistant import loader
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.service import async_get_all_descriptions


@pytest.mark.asyncio
async def test_service_exists(pyscript, hass):
    """Test discover, compile script and install a service."""
    await pyscript.start("""
@service
def func1():
    pass

# make sure a double definition still keeps the service registered
@service
@service("other.func1_renamed")
def func1():
    pass

def func2():
    pass
""")

    assert hass.services.has_service("pyscript", "func1")
    assert hass.services.has_service("other", "func1_renamed")
    assert hass.services.has_service("pyscript", "reload")
    assert not hass.services.has_service("pyscript", "func2")


@pytest.mark.asyncio
async def test_service_description(pyscript, hass):
    """Test service description defined in doc_string."""
    await pyscript.start("""
@service
def func_no_doc_string(param1=None):
    pass

@service
def func_simple_doc_string(param2=None, param3=None):
    \"\"\"This is func2_simple_doc_string.\"\"\"
    pass

@service
def func_yaml_doc_string(param2=None, param3=None):
    \"\"\"yaml
description: This is func_yaml_doc_string.
fields:
  param1:
    description: first argument
    example: 12
  param2:
    description: second argument
    example: 34
\"\"\"
    pass
""")

    integration = loader.Integration(
        hass,
        "custom_components.pyscript",
        pathlib.Path("custom_components/pyscript"),
        {"name": "pyscript", "dependencies": [], "requirements": [], "domain": "automation"},
    )

    with patch(
        "homeassistant.loader.async_get_custom_components",
        return_value={"pyscript": integration},
    ):
        descriptions = (await async_get_all_descriptions(hass))[DOMAIN]

    assert descriptions["func_no_doc_string"]["description"] == "pyscript function func_no_doc_string()"
    assert descriptions["func_no_doc_string"]["fields"] == {"param1": {"description": "argument param1"}}

    assert descriptions["func_simple_doc_string"]["description"] == "This is func2_simple_doc_string."
    assert descriptions["func_simple_doc_string"]["fields"] == {
        "param2": {"description": "argument param2"},
        "param3": {"description": "argument param3"},
    }

    assert descriptions["func_yaml_doc_string"]["description"] == "This is func_yaml_doc_string."
    assert descriptions["func_yaml_doc_string"]["fields"] == {
        "param1": {"description": "first argument", "example": 12},
        "param2": {"description": "second argument", "example": 34},
    }


@pytest.mark.asyncio
async def test_service_run(pyscript, hass, caplog):
    """Test running a service with keyword arguments."""
    await pyscript.start("""

@service
def func1(arg1=1, arg2=2, context=None):
    x = 1
    x = 2 * x + 3
    log.info(f"this is func1 x = {x}, arg1 = {arg1}, arg2 = {arg2}")
    pyscript.done = [x, arg1, arg2, str(context)]

# registering twice will cause it to be called twice
@service("other.func2")
@service("other.func2")
@service
def func2(**kwargs):
    x = 1
    x = 2 * x + 3
    log.info(f"this is func1 x = {x}, kwargs = {kwargs}")
    has2 = service.has_service("pyscript", "func2")
    has3 = service.has_service("pyscript", "func3")
    del kwargs["context"]
    pyscript.done = [x, kwargs, has2, has3]

@service
def call_service(domain=None, name=None, **kwargs):
    if domain == "pyscript" and name == "func1":
        task.sleep(0)
        pyscript.func1(**kwargs)
    else:
        service.call(domain, name, **kwargs)

""")

    context = Context(user_id="1234", parent_id="5678", id="8901")
    await hass.services.async_call("pyscript", "func1", {}, context=context)
    await pyscript.wait_done([5, 1, 2, str(context)])
    assert "this is func1 x = 5" in caplog.text

    await hass.services.async_call("pyscript", "func1", {"arg1": 10}, context=context)
    await pyscript.wait_done([5, 10, 2, str(context)])

    await hass.services.async_call(
        "pyscript",
        "call_service",
        {"domain": "pyscript", "name": "func1", "arg1": "string1"},
        context=context,
    )
    await pyscript.wait_done([5, "string1", 2, str(context)])

    await hass.services.async_call("pyscript", "func1", {"arg1": "string1", "arg2": 123}, context=context)
    await pyscript.wait_done([5, "string1", 123, str(context)])

    await hass.services.async_call("pyscript", "call_service", {"domain": "pyscript", "name": "func2"})
    await pyscript.wait_done([5, {"trigger_type": "service"}, 1, 0])

    await hass.services.async_call(
        "pyscript",
        "call_service",
        {"domain": "pyscript", "name": "func2", "arg1": "string1"},
    )
    await pyscript.wait_done([5, {"trigger_type": "service", "arg1": "string1"}, 1, 0])

    await hass.services.async_call("pyscript", "func2", {"arg1": "string1", "arg2": 456})
    await pyscript.wait_done([5, {"trigger_type": "service", "arg1": "string1", "arg2": 456}, 1, 0])

    await hass.services.async_call("other", "func2", {"arg1": "string1", "arg2": 123})
    await pyscript.wait_done([5, {"trigger_type": "service", "arg1": "string1", "arg2": 123}, 1, 0])


@pytest.mark.asyncio
async def test_service_reload_error(pyscript):
    """Test using a reserved name generates an error."""
    await pyscript.start("""
@service
def reload():
    pass
""")

    await pyscript.wait_exception(
        SyntaxError, match="function 'reload' defined in file.hello: @service conflicts with builtin service"
    )


@pytest.mark.asyncio
async def test_service_wrong_arg_type(pyscript):
    """Test a non-string service name generates an error."""
    await pyscript.start("""
@service(1)
def func5():
    pass
""")

    await pyscript.wait_exception(
        TypeError,
        match="function 'func5' defined in file.hello: decorator @service argument 1 should be a string",
    )


@pytest.mark.asyncio
async def test_service_state_active_blocks_call(pyscript, hass):
    """@state_active skips the service call and the caller gets no response."""
    hass.states.async_set("pyscript.enabled", "off")
    await pyscript.start("""
@service(supports_response="optional")
@state_active("pyscript.enabled == 'on'")
def guarded(x=None):
    pyscript.done = ["ran", x]
    return {"x": x}
""")

    assert await hass.services.async_call("pyscript", "guarded", {"x": 1}, blocking=True) is None
    # like HA scripts, a skipped call gives an empty response when one is requested
    response = await hass.services.async_call(
        "pyscript", "guarded", {"x": 2}, blocking=True, return_response=True
    )
    assert response == {}
    assert pyscript.done.empty()

    hass.states.async_set("pyscript.enabled", "on")
    response = await hass.services.async_call(
        "pyscript", "guarded", {"x": 3}, blocking=True, return_response=True
    )
    assert response == {"x": 3}
    await pyscript.wait_done(["ran", 3])


@pytest.mark.asyncio
async def test_service_time_active_blocks_call(pyscript, hass):
    """@time_active applies to service calls."""
    await pyscript.start("""
@service
@time_active("range(13:00, 14:00)")
def inactive():
    pyscript.done = "inactive ran"

@service
@time_active("range(11:00, 13:00)")
def active():
    pyscript.done = "active ran"
""")

    await hass.services.async_call("pyscript", "inactive", {}, blocking=True)
    await hass.services.async_call("pyscript", "active", {}, blocking=True)
    await pyscript.wait_done("active ran")


@pytest.mark.asyncio
async def test_service_task_unique_kill_me(pyscript, hass):
    """@task_unique(kill_me=True) skips a service call while the previous one is running."""
    hass.states.async_set("pyscript.release", "wait")
    await pyscript.start("""
@service
@task_unique("svc_kill_me", kill_me=True)
def slow(n=None):
    pyscript.done = ["started", n]
    task.wait_until(state_trigger="pyscript.release == 'go'")
    pyscript.done = ["finished", n]
""")

    await hass.services.async_call("pyscript", "slow", {"n": 1})
    await pyscript.wait_done(["started", 1])
    # the second call is skipped and returns right away; the fixture fails on an extra ["started", 2]
    await asyncio.wait_for(hass.services.async_call("pyscript", "slow", {"n": 2}, blocking=True), timeout=1)
    hass.states.async_set("pyscript.release", "go")
    await pyscript.wait_done(["finished", 1])


@pytest.mark.asyncio
async def test_service_task_unique_cancelled_call_returns(pyscript, hass):
    """A service call cancelled by a newer @task_unique call returns instead of hanging."""
    await pyscript.start("""
@service
@task_unique("svc_restart")
def restart(n=None):
    pyscript.done = ["started", n]
    if n == 1:
        task.sleep(10)  # still running when the second call cancels it
    pyscript.done = ["finished", n]
""")

    first = hass.async_create_task(hass.services.async_call("pyscript", "restart", {"n": 1}, blocking=True))
    await pyscript.wait_done(["started", 1])
    await hass.services.async_call("pyscript", "restart", {"n": 2}, blocking=True)
    await pyscript.wait_done(["started", 2])
    await pyscript.wait_done(["finished", 2])
    assert await asyncio.wait_for(first, timeout=1) is None


@pytest.mark.asyncio
async def test_service_call_cancelled_by_caller_cancels_function(pyscript, hass):
    """Cancelling a blocking service call (e.g. a restarted script) also cancels the running function."""
    await pyscript.start("""
@service
def slow():
    pyscript.done = "started"
    try:
        task.sleep(10)
    finally:
        pyscript.done = "stopped"
""")

    call = hass.async_create_task(hass.services.async_call("pyscript", "slow", {}, blocking=True))
    await pyscript.wait_done("started")
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    # the function is cancelled inside task.sleep() and reports it from its finally block
    await pyscript.wait_done("stopped", timeout=1)


@pytest.mark.asyncio
async def test_service_no_result_gives_empty_response(pyscript, hass):
    """A function returning nothing gives an empty response when one is requested, like HA scripts."""
    await pyscript.start("""
@service(supports_response="optional")
def no_result():
    pass
""")

    assert await hass.services.async_call("pyscript", "no_result", {}, blocking=True) is None
    response = await hass.services.async_call(
        "pyscript", "no_result", {}, blocking=True, return_response=True
    )
    assert response == {}


@pytest.mark.asyncio
async def test_service_exception_fails_call(pyscript, hass):
    """An exception in the function fails the service call and is still logged by pyscript."""
    await pyscript.start("""
@service
def broken():
    return 1 / 0
""")

    with pytest.raises(HomeAssistantError, match=r"broken\(\) raised ZeroDivisionError") as excinfo:
        await hass.services.async_call("pyscript", "broken", {}, blocking=True)
    assert isinstance(excinfo.value.__cause__, ZeroDivisionError)
    await pyscript.wait_exception(ZeroDivisionError)
