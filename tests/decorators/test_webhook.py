"""Test pyscript @webhook_trigger and @webhook_handler decorators."""

import asyncio
import json

import pytest

from custom_components.pyscript.const import CONF_LEGACY_DECORATORS, DOMAIN
from homeassistant.components import webhook
from homeassistant.helpers.json import json_bytes
from homeassistant.util.aiohttp import MockRequest


def _request(
    *,
    body: bytes = b"",
    method: str = "POST",
    headers: dict[str, str] | None = None,
) -> MockRequest:
    return MockRequest(
        content=body,
        mock_source="test",
        method=method,
        headers=headers or {},
        remote="127.0.0.1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_webhook_request_kwarg(pyscript, hass, legacy):
    """The aiohttp request is passed to the user function as the `request` kwarg."""
    running = []
    hass.bus.async_listen("pyscript_running", running.append)
    pyscript.config = {DOMAIN: {CONF_LEGACY_DECORATORS: legacy}}
    await pyscript.start("""
@webhook_trigger("test_req_hook")
def webhook_test(payload, request):
    pyscript.done = [request.headers["X-My-Sig"], request.method, payload]

@webhook_trigger("test_no_req_hook")
def webhook_no_request(payload):
    pyscript.done = payload
""")

    request = _request(
        body=b'{"hello": "world"}',
        headers={"Content-Type": "application/json", "X-My-Sig": "abc123"},
    )
    await webhook.async_handle_webhook(hass, "test_req_hook", request)
    await pyscript.wait_done(["abc123", "POST", {"hello": "world"}])

    # functions that don't declare request still work
    request = _request(body=b'{"a": 1}', headers={"Content-Type": "application/json"})
    await webhook.async_handle_webhook(hass, "test_no_req_hook", request)
    await pyscript.wait_done({"a": 1})

    # only the func_args the logbook uses are kept in the pyscript_running event
    await hass.async_block_till_done()
    assert len(running) == 2
    for event in running:
        assert event.data["func_args"] == {"trigger_type": "webhook"}
        json_bytes(event.data)


@pytest.mark.asyncio
async def test_webhook_handler_running_event(pyscript, hass):
    """The pyscript_running event for @webhook_handler is JSON serializable."""
    running = []
    hass.bus.async_listen("pyscript_running", running.append)
    await pyscript.start("""
@webhook_handler("handler_hook")
def webhook_handler_test(payload, request):
    return {"method": request.method, "payload": payload}
""")

    request = _request(body=b'{"b": 2}', headers={"Content-Type": "application/json"})
    response = await webhook.async_handle_webhook(hass, "handler_hook", request)
    assert json.loads(response.body) == {"method": "POST", "payload": {"b": 2}}

    await hass.async_block_till_done()
    assert len(running) == 1
    assert running[0].data["func_args"] == {"trigger_type": "webhook"}
    json_bytes(running[0].data)


@pytest.mark.asyncio
async def test_webhook_methods_order(pyscript):
    """Same webhook_id with methods listed in a different order is not a conflict."""
    await pyscript.start("""
@webhook_trigger("order_hook", methods=["GET", "POST"])
def func_order_a():
    pass

@webhook_trigger("order_hook", methods=["POST", "GET"])
def func_order_b():
    pass
""")
    assert pyscript.exceptions.empty()


@pytest.mark.asyncio
async def test_webhooks_method(pyscript):
    """Test invalid keyword arguments type generates an error."""
    await pyscript.start("""
@webhook_trigger("hook", methods=["bad"])
def func8():
    pass
""")
    await pyscript.wait_exception(TypeError, match="func8' defined in file.hello")


@pytest.mark.asyncio
async def test_webhook_local_only_conflict(pyscript):
    """Test @webhook_trigger with same webhook_id but conflicting local_only raises."""
    await pyscript.start("""
@webhook_trigger("conflict_local", local_only=True)
def func_local_a():
    pass

@webhook_trigger("conflict_local", local_only=False)
def func_local_b():
    pass
""")
    await pyscript.wait_exception(ValueError, match="'conflict_local' conflicts with existing")


@pytest.mark.asyncio
async def test_webhook_methods_conflict(pyscript):
    """Test @webhook_trigger with same webhook_id but conflicting methods raises."""
    await pyscript.start("""
@webhook_trigger("conflict_methods", methods=["GET"])
def func_methods_a():
    pass

@webhook_trigger("conflict_methods", methods=["POST"])
def func_methods_b():
    pass
""")
    await pyscript.wait_exception(ValueError, match="'conflict_methods' conflicts with existing")


@pytest.mark.asyncio
async def test_webhook_methods_missing_vs_set_conflict(pyscript):
    """Test @webhook_trigger with same webhook_id but only one specifying methods raises."""
    await pyscript.start("""
@webhook_trigger("conflict_unset")
def func_unset_a():
    pass

@webhook_trigger("conflict_unset", methods=["POST"])
def func_unset_b():
    pass
""")
    await pyscript.wait_exception(ValueError, match="'conflict_unset' conflicts with existing")


# --- @webhook_handler ---


@pytest.mark.asyncio
async def test_webhook_handler_responses(pyscript, hass):
    """Exercise the @webhook_handler return-value mapping and error paths end-to-end."""
    await pyscript.start("""
@webhook_handler("empty")
def webhook_empty(**_):
    pass

@webhook_handler("status_with_body")
def webhook_status_with_body(**_):
    return (201, {"id": 7})

@webhook_handler("status_with_none")
def webhook_status_with_none(**_):
    return (204, None)

@webhook_handler("text_hook")
def webhook_text(**_):
    return "hello"

@webhook_handler("json_hook")
def webhook_json(**_):
    return {"hello": "world"}

@webhook_handler("bad_json")
def webhook_bad_json(**_):
    # complex is not JSON-serializable
    return {"value": 1+2j}

@webhook_handler("bytes_hook")
def webhook_bytes(**_):
    return b"\\x00\\x01raw"

@webhook_handler("unsupported")
def webhook_unsupported(**_):
    return 42

@webhook_handler("bad_tuple")
def webhook_bad_tuple(**_):
    # 2-tuple but the first element isn't a status int
    return ("ok", "body")

@webhook_handler("crash")
def webhook_crash(**_):
    raise ValueError("boom")

@webhook_handler("parse")
def webhook_parse(**_):
    pyscript.done = "should not run"
    return (200, None)

@webhook_handler("req_handler")
def webhook_req(payload, request, **_):
    return {"sig": request.headers["X-My-Sig"], "method": request.method, "payload": payload}
""")

    # no return -> 200
    response = await webhook.async_handle_webhook(hass, "empty", _request())
    assert response.status == 200

    # (status, body) tuple -> status from tuple, body rendered as JSON
    response = await webhook.async_handle_webhook(hass, "status_with_body", _request())
    assert response.status == 201
    assert response.content_type == "application/json"
    assert json.loads(response.text) == {"id": 7}

    # (status, None) tuple -> status from tuple, empty body
    response = await webhook.async_handle_webhook(hass, "status_with_none", _request())
    assert response.status == 204

    # str -> 200 with text body
    response = await webhook.async_handle_webhook(hass, "text_hook", _request())
    assert response.status == 200
    assert response.text == "hello"

    # dict -> 200 with JSON body
    response = await webhook.async_handle_webhook(hass, "json_hook", _request())
    assert response.status == 200
    assert response.content_type == "application/json"
    assert json.loads(response.text) == {"hello": "world"}

    # non-JSON-serializable dict -> 500, also surfaced via log_exception
    response = await webhook.async_handle_webhook(hass, "bad_json", _request())
    assert response.status == 500
    await pyscript.wait_exception(TypeError, match="not JSON serializable")

    # bytes -> 200 with raw body
    response = await webhook.async_handle_webhook(hass, "bytes_hook", _request())
    assert response.status == 200
    assert response.body == b"\x00\x01raw"

    # unsupported return type (e.g. bare int) -> 500
    response = await webhook.async_handle_webhook(hass, "unsupported", _request())
    assert response.status == 500

    # 2-tuple with non-int status -> falls through to unsupported -> 500
    response = await webhook.async_handle_webhook(hass, "bad_tuple", _request())
    assert response.status == 500

    # uncaught exception -> 500, also surfaced via log_exception
    response = await webhook.async_handle_webhook(hass, "crash", _request())
    assert response.status == 500
    await pyscript.wait_exception(match="boom")

    # malformed JSON -> 400 without invoking the function
    response = await webhook.async_handle_webhook(
        hass,
        "parse",
        _request(body=b"{not json", headers={"Content-Type": "application/json"}),
    )
    assert response.status == 400
    assert pyscript.done.empty()

    # payload / request forwarded; return value becomes the response
    response = await webhook.async_handle_webhook(
        hass,
        "req_handler",
        _request(
            body=b'{"hello": "world"}',
            headers={"Content-Type": "application/json", "X-My-Sig": "abc123"},
        ),
    )
    assert response.status == 200
    assert json.loads(response.text) == {
        "sig": "abc123",
        "method": "POST",
        "payload": {"hello": "world"},
    }


@pytest.mark.asyncio
async def test_webhook_handler_concurrent_requests(pyscript, hass):
    """
    Two in-flight requests must each receive their own response with no crosstalk.

    The slow request is launched first but the fast one finishes first, so a
    naive implementation that stored the response future on the decorator
    instance instead of per-DispatchData would route the fast result to the
    slow caller (or vice versa).
    """
    await pyscript.start("""
@webhook_handler("concurrent")
def webhook_concurrent(payload, **_):
    task.sleep(payload["sleep"])
    return {"echo": payload["id"]}
""")

    slow, fast = await asyncio.gather(
        webhook.async_handle_webhook(
            hass,
            "concurrent",
            _request(
                body=b'{"id": "slow", "sleep": 0.1}',
                headers={"Content-Type": "application/json"},
            ),
        ),
        webhook.async_handle_webhook(
            hass,
            "concurrent",
            _request(
                body=b'{"id": "fast", "sleep": 0.02}',
                headers={"Content-Type": "application/json"},
            ),
        ),
    )

    assert slow.status == 200
    assert fast.status == 200
    assert json.loads(slow.text) == {"echo": "slow"}
    assert json.loads(fast.text) == {"echo": "fast"}


@pytest.mark.parametrize("expected_lingering_tasks", [True])
@pytest.mark.asyncio
async def test_webhook_handler_timeout(pyscript, hass):
    """
    A function that doesn't finish in time -> 504 Gateway Timeout.

    The pyscript task running the function lingers past the test (it's still
    sleeping when the webhook returns), so we opt in to HA's lingering-task
    allowance for this test only.
    """
    await pyscript.start("""
@webhook_handler("slow", timeout=0.05)
def webhook_slow(**_):
    task.sleep(0.15)
    return 200
""")
    response = await webhook.async_handle_webhook(hass, "slow", _request())
    assert response.status == 504


@pytest.mark.asyncio
async def test_webhook_handler_expression_rejects(pyscript, hass):
    """A str_expr that evaluates falsy -> 403 Forbidden, function is not invoked."""
    await pyscript.start("""
@webhook_handler("guarded", "payload.get('token') == 'secret'")
def webhook_guarded(payload, **_):
    pyscript.done = payload["token"]
    return {"ok": True}
""")

    # token missing -> guard rejects -> 403, function is not invoked
    response = await webhook.async_handle_webhook(
        hass,
        "guarded",
        _request(body=b'{"token": "wrong"}', headers={"Content-Type": "application/json"}),
    )
    assert response.status == 403
    assert pyscript.done.empty()

    # token matches -> function runs
    response = await webhook.async_handle_webhook(
        hass,
        "guarded",
        _request(body=b'{"token": "secret"}', headers={"Content-Type": "application/json"}),
    )
    assert response.status == 200
    assert json.loads(response.text) == {"ok": True}
    await pyscript.wait_done("secret")


@pytest.mark.asyncio
async def test_webhook_handler_duplicate_id(pyscript):
    """Two @webhook_handler with the same id conflict at registration."""
    await pyscript.start("""
@webhook_handler("dup")
def webhook_dup_a(**_):
    pass

@webhook_handler("dup")
def webhook_dup_b(**_):
    pass
""")
    await pyscript.wait_exception(ValueError, match="Handler is already defined")
