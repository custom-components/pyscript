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
from custom_components.pyscript.decorator_abc import DispatchData
from custom_components.pyscript.decorators.webhook import WebhookTriggerDecorator
from custom_components.pyscript.decorators.webhook_handler import (
    _RESPONSE_FUTURE_KEY,
    WebhookHandlerDecorator,
)
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
async def test_decorator_errors(hass):
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


def _post_webhook_request(content: bytes = b"") -> MockRequest:
    """Build a MockRequest representing a webhook POST with form data."""
    return MockRequest(
        content=content,
        headers={},
        method="POST",
        query_string="",
        mock_source="test",
        remote="127.0.0.1",
    )


def test_webhook_handler_coerce_response_none():
    """A None return should fall through to the HA default response."""
    assert WebhookHandlerDecorator.coerce_response(None) is None


def test_webhook_handler_coerce_response_int():
    """Int returns should produce an aiohttp Response with that status."""
    response = WebhookHandlerDecorator.coerce_response(HTTPStatus.CREATED.value)
    assert isinstance(response, web.Response)
    assert response.status == HTTPStatus.CREATED


def test_webhook_handler_coerce_response_passthrough():
    """An aiohttp Response should be returned unchanged."""
    custom = web.Response(status=HTTPStatus.ACCEPTED, body=b"queued")
    assert WebhookHandlerDecorator.coerce_response(custom) is custom


def test_webhook_handler_coerce_response_bool_warns(caplog):
    """Bool returns should be rejected so True/False don't masquerade as 1/0."""
    assert WebhookHandlerDecorator.coerce_response(True) is None
    assert "unsupported type bool" in caplog.text


def test_webhook_handler_coerce_response_unsupported_warns(caplog):
    """Other return types should warn and fall through."""
    assert WebhookHandlerDecorator.coerce_response("ok") is None
    assert "unsupported type str" in caplog.text


@pytest.mark.asyncio
async def test_webhook_handler_returns_status_code(hass):
    """A webhook handler returning an int should set the HTTP status."""
    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@webhook_handler("status_hook")
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
async def test_webhook_handler_default_response(hass):
    """A webhook handler returning None should produce a 200 OK."""
    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@webhook_handler("default_hook")
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
async def test_webhook_handler_exception_returns_500(hass, caplog):
    """A handler that raises should not hang and should return a 500."""
    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@webhook_handler("boom_hook")
def func_boom(payload):
    raise ValueError("boom")
""",
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    response = await asyncio.wait_for(
        webhook.async_handle_webhook(hass, "boom_hook", _post_webhook_request()), timeout=4
    )
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.INTERNAL_SERVER_ERROR
    # The underlying exception is still surfaced to the user's log.
    assert "boom" in caplog.text


@pytest.mark.asyncio
async def test_webhook_handler_exception_for_other_trigger_ignored():
    """An exception whose trigger is a different decorator must not resolve this future."""
    handler = Mock(spec=WebhookHandlerDecorator)
    future = asyncio.get_running_loop().create_future()
    data = DispatchData({}, trigger_context={_RESPONSE_FUTURE_KEY: future})
    data.trigger = object()  # a different trigger instance, not `handler`
    await WebhookHandlerDecorator.handle_call_exception(handler, data, ValueError("boom"))
    assert not future.done()


@pytest.mark.asyncio
async def test_webhook_handler_str_expr_no_match(hass):
    """When str_expr does not match, the function is not called and a 200 OK is sent."""
    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@webhook_handler("expr_hook", "payload['ok'] == 'yes'")
def func_expr(payload):
    return 418
""",
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    response = await webhook.async_handle_webhook(hass, "expr_hook", _post_webhook_request(b"ok=no"))
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.OK


@pytest.mark.asyncio
async def test_webhook_handler_str_expr_match(hass):
    """When str_expr matches, the function is called and its return value drives the response."""
    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@webhook_handler("expr_match_hook", "payload['ok'] == 'yes'")
def func_expr(payload):
    return 418
""",
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    response = await webhook.async_handle_webhook(hass, "expr_match_hook", _post_webhook_request(b"ok=yes"))
    await hass.async_block_till_done()
    assert response.status == 418  # the function returned 418 (HTTP "I'm a teapot")


@pytest.mark.asyncio
async def test_webhook_handler_json_payload(hass):
    """A JSON request body is parsed into the payload passed to the handler."""
    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@webhook_handler("json_hook")
def func_json(payload):
    return payload["status"]
""",
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    request = MockRequest(
        content=b'{"status": 503}',
        mock_source="test",
        method="POST",
        headers={"Content-Type": "application/json"},
        remote="127.0.0.1",
    )
    response = await webhook.async_handle_webhook(hass, "json_hook", request)
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_webhook_handler_concurrent_requests(hass):
    """Concurrent requests to the same id must each get their own response (no shared future)."""
    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@webhook_handler("concurrent_hook")
def func_concurrent(payload):
    task.sleep(float(payload["delay"]))
    return int(payload["code"])
""",
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    # Request A starts first but sleeps longer, so it finishes after request B. Under the old
    # shared-instance `self.future` design A would hang or receive B's response.
    response_a, response_b = await asyncio.gather(
        webhook.async_handle_webhook(hass, "concurrent_hook", _post_webhook_request(b"delay=0.3&code=201")),
        webhook.async_handle_webhook(hass, "concurrent_hook", _post_webhook_request(b"delay=0.05&code=202")),
    )
    await hass.async_block_till_done()
    assert response_a.status == HTTPStatus.CREATED
    assert response_b.status == HTTPStatus.ACCEPTED


@pytest.mark.asyncio
async def test_webhook_handler_cancelled_action_still_responds(hass):
    """If @task_unique cancels an in-flight action, that request must get a 500, not hang."""
    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@webhook_handler("cancel_hook")
@task_unique("cancel_unique")
def func_cancel(payload):
    task.sleep(float(payload["delay"]))
    return int(payload["code"])
""",
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    async def fire(delay, code):
        return await asyncio.wait_for(
            webhook.async_handle_webhook(
                hass, "cancel_hook", _post_webhook_request(f"delay={delay}&code={code}".encode())
            ),
            timeout=4,
        )

    # Both requests register the same @task_unique name while sleeping, so whichever runs
    # second cancels the other mid-flight. Before the fix the cancelled request's response
    # future was orphaned and the request hung; now it resolves to a 500.
    results = await asyncio.gather(fire(0.3, 201), fire(0.05, 202), return_exceptions=True)
    await hass.async_block_till_done()

    assert not any(isinstance(r, Exception) for r in results), f"a request hung/failed: {results}"
    statuses = sorted(r.status for r in results)
    assert statuses[0] in (HTTPStatus.CREATED, HTTPStatus.ACCEPTED), statuses  # survivor's own code
    assert statuses[1] == HTTPStatus.INTERNAL_SERVER_ERROR, statuses  # cancelled request


@pytest.mark.asyncio
async def test_webhook_handler_malformed_json_returns_400(hass):
    """A body that claims to be JSON but isn't should yield a 400, not a masked 200."""
    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@webhook_handler("bad_json_hook")
def func_bad_json(payload):
    return 200
""",
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    request = MockRequest(
        content=b"{not valid json",
        mock_source="test",
        method="POST",
        headers={"Content-Type": "application/json"},
        remote="127.0.0.1",
    )
    response = await webhook.async_handle_webhook(hass, "bad_json_hook", request)
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.BAD_REQUEST


def test_webhook_handler_coerce_response_out_of_range_warns(caplog):
    """An int outside the valid HTTP status range should warn and fall through to a 200."""
    assert WebhookHandlerDecorator.coerce_response(1000) is None
    assert WebhookHandlerDecorator.coerce_response(0) is None
    assert "not a valid HTTP status code" in caplog.text


def test_webhook_handler_duplicate_id_fails():
    """A second @webhook_handler for the same webhook_id is rejected by HA's webhook registry."""
    hass = Mock(data={})
    first = Mock(webhook_id="dup_hook", local_only=True, methods=None, dm=Mock(hass=hass))
    second = Mock(webhook_id="dup_hook", local_only=True, methods=None, dm=Mock(hass=hass))

    WebhookHandlerDecorator.webhook_id2handler.pop("dup_hook", None)
    try:
        WebhookHandlerDecorator._add_handler(first)  # pylint: disable=protected-access
        with pytest.raises(ValueError, match="Handler is already defined"):
            WebhookHandlerDecorator._add_handler(second)  # pylint: disable=protected-access
    finally:
        WebhookHandlerDecorator.webhook_id2handler.pop("dup_hook", None)


def test_webhook_handler_collides_with_webhook_trigger():
    """A @webhook_handler whose id is already used by a @webhook_trigger is rejected by HA."""
    hass = Mock(data={})
    trigger_mock = Mock(webhook_id="shared_hook", local_only=True, methods=None, dm=Mock(hass=hass))
    handler = Mock(webhook_id="shared_hook", local_only=True, methods=None, dm=Mock(hass=hass))

    WebhookTriggerDecorator.webhook_id2triggers.pop("shared_hook", None)
    WebhookHandlerDecorator.webhook_id2handler.pop("shared_hook", None)
    try:
        WebhookTriggerDecorator._add_trigger(trigger_mock)  # pylint: disable=protected-access
        with pytest.raises(ValueError, match="Handler is already defined"):
            WebhookHandlerDecorator._add_handler(handler)  # pylint: disable=protected-access
    finally:
        WebhookTriggerDecorator.webhook_id2triggers.pop("shared_hook", None)
        WebhookHandlerDecorator.webhook_id2handler.pop("shared_hook", None)


def test_webhook_handler_remove_unregisters_and_frees_id():
    """Removing a handler unregisters the webhook and frees the id for reuse."""
    handler = Mock(webhook_id="remove_hook", local_only=True, methods=None)

    WebhookHandlerDecorator.webhook_id2handler.pop("remove_hook", None)
    with (
        patch("custom_components.pyscript.decorators.webhook_handler.webhook.async_register") as register,
        patch(
            "custom_components.pyscript.decorators.webhook_handler.webhook.async_unregister"
        ) as unregister,
    ):
        try:
            WebhookHandlerDecorator._add_handler(handler)  # pylint: disable=protected-access
            assert register.called
            assert WebhookHandlerDecorator.webhook_id2handler["remove_hook"] is handler

            WebhookHandlerDecorator._remove_handler(handler)  # pylint: disable=protected-access
            assert unregister.called
            assert "remove_hook" not in WebhookHandlerDecorator.webhook_id2handler
        finally:
            WebhookHandlerDecorator.webhook_id2handler.pop("remove_hook", None)


@pytest.mark.asyncio
async def test_webhook_handler_unknown_id_returns_none():
    """The shared handler returns None for a webhook_id with no registered handler."""
    WebhookHandlerDecorator.webhook_id2handler.pop("ghost_hook", None)
    result = await WebhookHandlerDecorator._handler(  # pylint: disable=protected-access
        Mock(), "ghost_hook", Mock()
    )
    assert result is None


def test_webhook_handler_remove_is_noop_for_unregistered():
    """Removing a handler that isn't the registered one must not unregister anything."""
    handler = Mock(webhook_id="absent_hook")
    WebhookHandlerDecorator.webhook_id2handler.pop("absent_hook", None)
    with patch(
        "custom_components.pyscript.decorators.webhook_handler.webhook.async_unregister"
    ) as unregister:
        WebhookHandlerDecorator._remove_handler(handler)  # pylint: disable=protected-access
    assert not unregister.called


@pytest.mark.asyncio
async def test_webhook_handler_result_for_other_trigger_ignored():
    """A result whose trigger is a different decorator must not resolve this handler's future."""
    handler = Mock(spec=WebhookHandlerDecorator)
    future = asyncio.get_running_loop().create_future()
    data = DispatchData({}, trigger_context={_RESPONSE_FUTURE_KEY: future})
    data.trigger = object()  # a different trigger instance, not `handler`
    await WebhookHandlerDecorator.handle_call_result(handler, data, 201)
    assert not future.done()
