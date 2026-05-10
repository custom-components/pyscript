"""Test pyscript user-defined decorators."""

from ast import literal_eval
import asyncio
from datetime import datetime as dt
from http import HTTPStatus
from unittest.mock import Mock, mock_open, patch

from aiohttp import web
import pytest

from custom_components.pyscript import trigger
from custom_components.pyscript.const import DOMAIN
from custom_components.pyscript.decorators.webhook import WebhookTriggerDecorator
from custom_components.pyscript.function import Function
from homeassistant.components import webhook
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED
from homeassistant.setup import async_setup_component
from homeassistant.util.aiohttp import MockRequest


async def setup_script(hass, notify_q, now, source):
    """Initialize and load the given pyscript."""
    scripts = [
        "/hello.py",
    ]

    Function.hass = None

    with (
        patch("custom_components.pyscript.os.path.isdir", return_value=True),
        patch("custom_components.pyscript.glob.iglob", return_value=scripts),
        patch("custom_components.pyscript.global_ctx.open", mock_open(read_data=source)),
        patch("custom_components.pyscript.trigger.dt_now", return_value=now),
        patch("homeassistant.config.load_yaml_config_file", return_value={}),
        patch("custom_components.pyscript.open", mock_open(read_data=source)),
        patch("custom_components.pyscript.watchdog_start", return_value=None),
        patch("custom_components.pyscript.os.path.getmtime", return_value=1000),
        patch("custom_components.pyscript.global_ctx.os.path.getmtime", return_value=1000),
        patch(
            "custom_components.pyscript.install_requirements",
            return_value=None,
        ),
    ):
        assert await async_setup_component(hass, "pyscript", {DOMAIN: {}})

    #
    # I'm not sure how to run the mock all the time, so just force the dt_now()
    # trigger function to return the given list of times in now.
    #
    def return_next_time():
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


@pytest.mark.asyncio
async def test_decorator_errors(hass, caplog):
    """Test decorator syntax and run-time errors."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        [dt(2020, 7, 1, 11, 59, 59, 999999)],
        """
seq_num = 0

def add_startup_trig(func):
    @time_trigger("startup")
    def dec_add_startup_wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return dec_add_startup_wrapper

def once(func):
    def once_func(*args, **kwargs):
        return func(*args, **kwargs)
    return once_func

def twice(func):
    def twice_func(*args, **kwargs):
        func(*args, **kwargs)
        return func(*args, **kwargs)
    return twice_func

@twice
@add_startup_trig
@twice
def func_startup_sync(trigger_type=None, trigger_time=None):
    global seq_num

    seq_num += 1
    log.info(f"func_startup_sync setting pyscript.done = {seq_num}, trigger_type = {trigger_type}, trigger_time = {trigger_time}")
    pyscript.done = seq_num

trig_list = ["pyscript.var1 == '100'", "pyscript.var1 == '1'"]
@state_trigger(*trig_list)
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
    num_times += 0
    def decorator_repeat(func):
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

@state_trigger("pyscript.var1 == '5'")
def func5(value=None):
    global seq_num, startup_test

    seq_num += 1
    pyscript.done = [seq_num, int(value)]

    @add_startup_trig
    def startup_test():
        global seq_num

        seq_num += 1
        pyscript.done = [seq_num, int(value)]

def add_state_trig(value):
    def dec_add_state_trig(func):
        @state_trigger(f"pyscript.var1 == '{value}'")
        def dec_add_state_wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return dec_add_state_wrapper
    return dec_add_state_trig

@add_state_trig(6)              # same as @state_trigger("pyscript.var1 == '6'")
@add_state_trig(8)              # same as @state_trigger("pyscript.var1 == '8'")
@state_trigger("pyscript.var1 == '10'")
def func6(value):
    global seq_num

    seq_num += 1
    pyscript.done = [seq_num, int(value)]

""",
    )
    seq_num = 0

    # fire event to start triggers, and handshake when they are running
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    for _ in range(4):
        seq_num += 1
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

    hass.states.async_set("pyscript.var1", 5)
    for _ in range(2):
        seq_num += 1
        assert literal_eval(await wait_until_done(notify_q)) == [seq_num, 5]

    for i in range(3):
        hass.states.async_set("pyscript.var1", 6 + 2 * i)
        seq_num += 1
        assert literal_eval(await wait_until_done(notify_q)) == [seq_num, 6 + 2 * i]


@pytest.mark.asyncio
async def test_webhook_request_kwarg(hass):
    """The aiohttp request is passed to the user function as the `request` kwarg."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        [dt(2020, 7, 1, 11, 59, 59, 999999)],
        """
@webhook_trigger("test_req_hook")
def webhook_test(payload, request):
    pyscript.done = [request.headers["X-My-Sig"], request.method, payload]
""",
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    request = MockRequest(
        content=b'{"hello": "world"}',
        mock_source="test",
        method="POST",
        headers={"Content-Type": "application/json", "X-My-Sig": "abc123"},
        remote="127.0.0.1",
    )

    await webhook.async_handle_webhook(hass, "test_req_hook", request)

    assert literal_eval(await wait_until_done(notify_q)) == ["abc123", "POST", {"hello": "world"}]


def _post_webhook_request() -> MockRequest:
    """Build a MockRequest representing a webhook POST with form data."""
    return MockRequest(
        content=b"",
        headers={},
        method="POST",
        query_string="",
        mock_source="test",
        remote="127.0.0.1",
    )


def test_webhook_coerce_response_none():
    """A None return should fall through to the HA default response."""
    assert WebhookTriggerDecorator.coerce_response(None) is None


def test_webhook_coerce_response_int():
    """Int returns should produce an aiohttp Response with that status."""
    response = WebhookTriggerDecorator.coerce_response(HTTPStatus.CREATED.value)
    assert isinstance(response, web.Response)
    assert response.status == HTTPStatus.CREATED


def test_webhook_coerce_response_passthrough():
    """An aiohttp Response should be returned unchanged."""
    custom = web.Response(status=HTTPStatus.ACCEPTED, body=b"queued")
    assert WebhookTriggerDecorator.coerce_response(custom) is custom


def test_webhook_coerce_response_bool_warns(caplog):
    """Bool returns should be rejected so True/False don't masquerade as 1/0."""
    assert WebhookTriggerDecorator.coerce_response(True) is None
    assert "unsupported type bool" in caplog.text


def test_webhook_coerce_response_unsupported_warns(caplog):
    """Other return types should warn and fall through."""
    assert WebhookTriggerDecorator.coerce_response("ok") is None
    assert "unsupported type str" in caplog.text


@pytest.mark.asyncio
async def test_webhook_function_returns_status_code(hass):
    """A flagged webhook function returning an int should set the HTTP status."""
    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@webhook_trigger("status_hook", sets_http_response_code=True)
def func_status(payload):
    return 201
""",
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    response = await webhook.async_handle_webhook(hass, "status_hook", _post_webhook_request())
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.CREATED


@pytest.mark.asyncio
async def test_webhook_function_default_response(hass):
    """A pyscript webhook function returning None should produce a 200 OK."""
    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@webhook_trigger("default_hook")
def func_default(payload):
    pass
""",
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    response = await webhook.async_handle_webhook(hass, "default_hook", _post_webhook_request())
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.OK


@pytest.mark.asyncio
async def test_webhook_unflagged_return_ignored(hass):
    """A return value from a trigger without sets_http_response_code is ignored."""
    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@webhook_trigger("unflagged_hook")
def func_unflagged(payload):
    return 418
""",
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    response = await webhook.async_handle_webhook(hass, "unflagged_hook", _post_webhook_request())
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.OK


@pytest.mark.asyncio
async def test_webhook_only_flagged_trigger_controls_response(hass):
    """When multiple triggers share an id, only the flagged one drives the response."""
    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@webhook_trigger("multi_hook")
def func_silent(payload):
    return 500

@webhook_trigger("multi_hook", sets_http_response_code=True)
def func_loud(payload):
    return 418
""",
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    response = await webhook.async_handle_webhook(hass, "multi_hook", _post_webhook_request())
    await hass.async_block_till_done()
    assert response.status == 418


@pytest.mark.asyncio
async def test_webhook_flag_is_per_webhook_id(hass):
    """Different webhook_ids may each have their own sets_http_response_code=True trigger."""
    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@webhook_trigger("hook_a", sets_http_response_code=True)
def func_a(payload):
    return 201

@webhook_trigger("hook_b", sets_http_response_code=True)
def func_b(payload):
    return 202
""",
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    response_a = await webhook.async_handle_webhook(hass, "hook_a", _post_webhook_request())
    response_b = await webhook.async_handle_webhook(hass, "hook_b", _post_webhook_request())
    await hass.async_block_till_done()
    assert response_a.status == HTTPStatus.CREATED
    assert response_b.status == HTTPStatus.ACCEPTED


def test_webhook_multiple_flagged_triggers_fails_at_setup():
    """A second flagged trigger for the same webhook_id should be rejected at start."""
    first = Mock(webhook_id="dup_hook", sets_http_response_code=True)
    second = Mock(webhook_id="dup_hook", sets_http_response_code=True)

    WebhookTriggerDecorator.webhook_id2triggers.pop("dup_hook", None)
    WebhookTriggerDecorator.webhook_id2triggers["dup_hook"] = {first}
    try:
        with pytest.raises(ValueError, match="sets_http_response_code=True"):
            WebhookTriggerDecorator._add_trigger(second)  # pylint: disable=protected-access
    finally:
        WebhookTriggerDecorator.webhook_id2triggers.pop("dup_hook", None)
