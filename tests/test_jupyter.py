"""Test the pyscript Jupyter kernel."""

from ast import literal_eval
import asyncio
from datetime import datetime as dt
import hashlib
import hmac
import json
import re
import uuid

from custom_components.pyscript.const import DOMAIN, FOLDER
from custom_components.pyscript.jupyter_kernel import ZmqSocket
import custom_components.pyscript.trigger as trigger
from mock_open import MockOpen
from pytest_homeassistant_custom_component.async_mock import patch

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.setup import async_setup_component

SECRET_KEY = b"0123456789abcdef"
DELIM = b"<IDS|MSG>"


def str_to_bytes(string):
    """Encode a string in bytes."""
    return string.encode("utf-8")


def msg_id():
    """Return a new uuid for message id."""
    return str(uuid.uuid4())


def msg_sign(msg_lst, secret_key=SECRET_KEY):
    """Sign a message with a secure signature."""
    auth_hmac = hmac.HMAC(secret_key, digestmod=hashlib.sha256)
    for msg in msg_lst:
        auth_hmac.update(msg)
    return str_to_bytes(auth_hmac.hexdigest())


def deserialize_wire_msg(wire_msg):
    """Split the routing prefix and message frames from a message on the wire."""
    delim_idx = wire_msg.index(DELIM)
    m_signature = wire_msg[delim_idx + 1]
    msg_frames = wire_msg[delim_idx + 2 :]

    def decode(msg):
        return json.loads(msg.decode("utf-8"))

    msg = {}
    msg["header"] = decode(msg_frames[0])
    msg["parent_header"] = decode(msg_frames[1])
    msg["metadata"] = decode(msg_frames[2])
    msg["content"] = decode(msg_frames[3])
    check_sig = msg_sign(msg_frames)
    assert check_sig == m_signature
    return msg


def new_header(msg_type):
    """Make a new header."""
    engine_id = str(uuid.uuid4())
    return {
        "date": dt.now().isoformat(),
        "msg_id": msg_id(),
        "username": "kernel",
        "session": engine_id,
        "msg_type": msg_type,
        "version": "5.3",
    }


async def send(
    zmq_sock,
    msg_type,
    content=None,
    parent_header=None,
    metadata=None,
    identities=None,
    secret_key=SECRET_KEY,
):
    """Send message to the Jupyter client."""
    header = new_header(msg_type)

    def encode(msg):
        return str_to_bytes(json.dumps(msg))

    msg_lst = [
        encode(header),
        encode(parent_header if parent_header else {}),
        encode(metadata if metadata else {}),
        encode(content if content else {}),
    ]
    signature = msg_sign(msg_lst, secret_key=secret_key)
    parts = [DELIM, signature, msg_lst[0], msg_lst[1], msg_lst[2], msg_lst[3]]
    if identities:
        parts = identities + parts
    if zmq_sock:
        await zmq_sock.send_multipart(parts)


IO_pub_msgs = {}

PORT_NAMES = ["hb_port", "stdin_port", "shell_port", "iopub_port", "control_port"]


async def setup_script(hass, now, source, no_connect=False):
    """Initialize and load the given pyscript."""

    conf_dir = hass.config.path(FOLDER)

    file_contents = {f"{conf_dir}/hello.py": source}

    mock_open = MockOpen()
    for key, value in file_contents.items():
        mock_open[key].read_data = value

    def isfile_side_effect(arg):
        return arg in file_contents

    def glob_side_effect(path, recursive=None):
        result = []
        path_re = path.replace("*", "[^/]*").replace(".", "\\.")
        path_re = path_re.replace("[^/]*[^/]*/", ".*")
        for this_path in file_contents:
            if re.match(path_re, this_path):
                result.append(this_path)
        return result

    with patch("custom_components.pyscript.os.path.isdir", return_value=True), patch(
        "custom_components.pyscript.glob.iglob"
    ) as mock_glob, patch("custom_components.pyscript.global_ctx.open", mock_open), patch(
        "custom_components.pyscript.trigger.dt_now", return_value=now
    ), patch(
        "custom_components.pyscript.open", mock_open
    ), patch(
        "homeassistant.config.load_yaml_config_file", return_value={}
    ), patch(
        "custom_components.pyscript.install_requirements", return_value=None,
    ), patch(
        "custom_components.pyscript.watchdog_start", return_value=None
    ), patch(
        "custom_components.pyscript.os.path.getmtime", return_value=1000
    ), patch(
        "custom_components.pyscript.global_ctx.os.path.getmtime", return_value=1000
    ), patch(
        "custom_components.pyscript.os.path.isfile"
    ) as mock_isfile:
        mock_isfile.side_effect = isfile_side_effect
        mock_glob.side_effect = glob_side_effect
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

    kernel_state_var = "pyscript.jupyter_ports_1234"
    kernel_cfg = {
        "ip": "127.0.0.1",
        "key": SECRET_KEY.decode("utf-8"),
        "signature_scheme": "hmac-sha256",
        "state_var": kernel_state_var,
    }
    if no_connect:
        kernel_cfg["no_connect_timeout"] = 0.0
    await hass.services.async_call("pyscript", "jupyter_kernel_start", kernel_cfg)

    while True:
        ports_state = hass.states.get(kernel_state_var)
        if ports_state is not None:
            break
        await asyncio.sleep(2e-3)

    port_nums = json.loads(ports_state.state)

    sock = {}

    if no_connect:
        return sock, port_nums

    for name in PORT_NAMES:
        kernel_reader, kernel_writer = await asyncio.open_connection("127.0.0.1", port_nums[name])
        sock[name] = ZmqSocket(kernel_reader, kernel_writer, "ROUTER")
        await sock[name].handshake()

    return sock, port_nums


async def shutdown(sock):
    """Shutdown the client session."""
    await send(sock["control_port"], "shutdown_request", {}, parent_header={}, identities={})
    reply = deserialize_wire_msg(await sock["control_port"].recv_multipart())
    assert reply["header"]["msg_type"] == "shutdown_reply"

    for zmq_sock in sock.values():
        zmq_sock.close()


async def get_iopub_msg(sock, msg_types):
    """Get next iopub message of a particular type, and queue others."""
    msg_types = {msg_types} if not isinstance(msg_types, set) else msg_types
    for msg_type in msg_types:
        if msg_type in IO_pub_msgs and len(IO_pub_msgs[msg_type]) > 0:
            return IO_pub_msgs[msg_type].pop(0)
    while True:
        msg = deserialize_wire_msg(await sock["iopub_port"].recv_multipart())
        if msg["header"]["msg_type"] in msg_types:
            return msg
        if msg["header"]["msg_type"] not in IO_pub_msgs:
            IO_pub_msgs[msg["header"]["msg_type"]] = []
        IO_pub_msgs[msg["header"]["msg_type"]].append(msg)


async def shell_msg(sock, msg_type, msg_content, execute=False):
    """Send a shell command and receive the results."""
    #
    # With execute=True, it executes code, but expects a result reply
    # or error.
    #
    await send(
        sock["shell_port"], msg_type, msg_content, parent_header={}, identities={},
    )
    #
    # we expect a busy status on iopub
    #
    status_msg = await get_iopub_msg(sock, "status")
    assert status_msg["content"]["execution_state"] == "busy"

    if execute:
        #
        # we expect execute_input and execute_result on iopub
        #
        msg = await get_iopub_msg(sock, "execute_input")
        assert msg["header"]["msg_type"] == "execute_input"

        reply_msg = await get_iopub_msg(sock, {"execute_result", "error"})

        msg = deserialize_wire_msg(await sock["shell_port"].recv_multipart())
        assert msg["header"]["msg_type"] == "execute_reply"

    else:
        reply_msg = deserialize_wire_msg(await sock["shell_port"].recv_multipart())

    #
    # we expect an idle status on iopub
    #
    status_msg = await get_iopub_msg(sock, "status")
    assert status_msg["content"]["execution_state"] == "idle"

    return reply_msg


async def test_jupyter_kernel_msgs(hass, caplog):
    """Test Jupyter kernel messages."""
    sock, _ = await setup_script(hass, [dt(2020, 7, 1, 11, 0, 0, 0)], "")

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    #
    # test the heartbeat loopback with some long and short messages
    # also send messages to stdin and iopub, which ignore them
    #
    for i in range(5):
        if i & 1:
            msg = (f"hello {i} " * 40).encode("utf-8")
        else:
            msg = f"hello {i}".encode("utf-8")
        await sock["hb_port"].send(msg)
        await sock["iopub_port"].send(msg)
        await sock["stdin_port"].send(msg)
        assert await sock["hb_port"].recv() == msg

    #
    # now send some shell messages and check the responses
    #
    reply = await shell_msg(sock, "kernel_info_request", {})
    assert reply["header"]["msg_type"] == "kernel_info_reply"

    reply = await shell_msg(sock, "comm_info_request", {})
    assert reply["header"]["msg_type"] == "comm_info_reply"

    reply = await shell_msg(sock, "history_request", {})
    assert reply["header"]["msg_type"] == "history_reply"

    #
    # test completions
    #
    code = "whi"
    reply = await shell_msg(sock, "complete_request", {"code": code, "cursor_pos": len(code)})
    assert reply["header"]["msg_type"] == "complete_reply"
    assert reply["content"]["matches"] == ["while"]

    #
    # test completions
    #
    code = "1+2\n3+4\nwhi"
    reply = await shell_msg(sock, "complete_request", {"code": code, "cursor_pos": len(code)})
    assert reply["header"]["msg_type"] == "complete_reply"
    assert reply["content"]["matches"] == ["while"]

    code = "pyscr"
    reply = await shell_msg(sock, "complete_request", {"code": code, "cursor_pos": len(code)})
    assert reply["header"]["msg_type"] == "complete_reply"
    assert reply["content"]["matches"] == [
        "pyscript",
        "pyscript.config",
        "pyscript.get_global_ctx",
        "pyscript.list_global_ctx",
        "pyscript.set_global_ctx",
    ]

    hass.states.async_set("pyscript.f1var1", 0)
    reply = await shell_msg(
        sock, "complete_request", {"code": "pyscript.f", "cursor_pos": len("pyscript.f")}
    )
    assert reply["header"]["msg_type"] == "complete_reply"
    assert reply["content"]["matches"] == ["pyscript.f1var1"]

    hass.states.async_set("pyscript.f1var1", 0, {"attr1": 5, "attr2": 10})
    reply = await shell_msg(
        sock, "complete_request", {"code": "pyscript.f1var1.a", "cursor_pos": len("pyscript.f1var1.a")}
    )
    assert reply["header"]["msg_type"] == "complete_reply"
    assert reply["content"]["matches"] == ["pyscript.f1var1.attr1", "pyscript.f1var1.attr2"]

    #
    # test is_complete
    #
    reply = await shell_msg(sock, "is_complete_request", {"code": "x = 1"})
    assert reply["header"]["msg_type"] == "is_complete_reply"
    assert reply["content"]["status"] == "complete"

    reply = await shell_msg(sock, "is_complete_request", {"code": "def func():\n    pass"})
    assert reply["header"]["msg_type"] == "is_complete_reply"
    assert reply["content"]["status"] == "incomplete"

    reply = await shell_msg(sock, "is_complete_request", {"code": "def func():\n    pass\n"})
    assert reply["header"]["msg_type"] == "is_complete_reply"
    assert reply["content"]["status"] == "complete"

    reply = await shell_msg(sock, "is_complete_request", {"code": "x = "})
    assert reply["header"]["msg_type"] == "is_complete_reply"
    assert reply["content"]["status"] == "invalid"

    reply = await shell_msg(sock, "is_complete_request", {"code": "if 1:\n"})
    assert reply["header"]["msg_type"] == "is_complete_reply"
    assert reply["content"]["status"] == "incomplete"

    reply = await shell_msg(sock, "is_complete_request", {"code": "if 1:    \n"})
    assert reply["header"]["msg_type"] == "is_complete_reply"
    assert reply["content"]["status"] == "incomplete"

    #
    # test code execution
    #
    reply = await shell_msg(sock, "execute_request", {"code": "x = 123; x + 1 + 2"}, execute=True)
    assert reply["content"]["data"]["text/plain"] == "126"

    reply = await shell_msg(sock, "execute_request", {"code": "import math; x + 5"}, execute=True)
    assert reply["content"]["data"]["text/plain"] == "128"

    #
    # do a reload to make sure our global context is preserved
    #
    with patch("homeassistant.config.load_yaml_config_file", return_value={}):
        await hass.services.async_call("pyscript", "reload", {}, blocking=True)

    reply = await shell_msg(sock, "execute_request", {"code": "x + 10"}, execute=True)
    assert reply["content"]["data"]["text/plain"] == "133"

    #
    # test completion of object attribute now that we've loaded math above
    #
    code = "import math; math.sq"
    reply = await shell_msg(sock, "complete_request", {"code": code, "cursor_pos": len(code)})
    assert reply["header"]["msg_type"] == "complete_reply"
    assert reply["content"]["matches"] == ["math.sqrt"]

    #
    # run-time error
    #
    reply = await shell_msg(sock, "execute_request", {"code": "xyz"}, execute=True)
    assert reply["content"]["evalue"] == "name 'xyz' is not defined"

    #
    # syntax error
    #
    reply = await shell_msg(sock, "execute_request", {"code": "1 + "}, execute=True)
    assert reply["content"]["evalue"] == "invalid syntax (jupyter_0, line 1)"

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)

    await shutdown(sock)


async def test_jupyter_kernel_port_close(hass, caplog):
    """Test Jupyter kernel closing ports."""
    sock, port_nums = await setup_script(hass, [dt(2020, 7, 1, 11, 0, 0, 0)], "")

    #
    # test the heartbeat loopback with some long and short messages
    # also send messages to stdin and iopub, which ignore them
    #
    for i in range(5):
        if i & 1:
            msg = (f"hello {i} " * 40).encode("utf-8")
        else:
            msg = f"hello {i}".encode("utf-8")
        await sock["hb_port"].send(msg)
        await sock["iopub_port"].send(msg)
        await sock["stdin_port"].send(msg)
        assert await sock["hb_port"].recv() == msg

    #
    # now close and re-open each por
    #
    for name in PORT_NAMES:
        sock[name].close()
        kernel_reader, kernel_writer = await asyncio.open_connection("127.0.0.1", port_nums[name])
        sock[name] = ZmqSocket(kernel_reader, kernel_writer, "ROUTER")
        await sock[name].handshake()

    #
    # test the heartbeat loopback again
    # also send messages to stdin and iopub, which ignore them
    #
    for i in range(5):
        if i & 1:
            msg = (f"hello {i} " * 40).encode("utf-8")
        else:
            msg = f"hello {i}".encode("utf-8")
        await sock["hb_port"].send(msg)
        await sock["iopub_port"].send(msg)
        await sock["stdin_port"].send(msg)
        assert await sock["hb_port"].recv() == msg

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)

    #
    # shut down the session via signature mismatch with bad key
    #
    await send(
        sock["control_port"], "shutdown_request", {}, parent_header={}, identities={}, secret_key=b"bad_key"
    )

    #
    # wait until the session ends, so the log receives the error message we check below
    #
    try:
        await sock["iopub_port"].recv()
    except EOFError:
        pass

    assert "signature mismatch: check_sig=" in caplog.text


async def test_jupyter_kernel_redefine_func(hass, caplog):
    """Test Jupyter kernel redefining trigger function."""
    sock, _ = await setup_script(hass, [dt(2020, 7, 1, 11, 0, 0, 0)], "")

    reply = await shell_msg(
        sock,
        "execute_request",
        {
            "code": """
@time_trigger("once(2019/9/7 12:00)")
@state_trigger("pyscript.var1 == '1'")
@event_trigger("test_event")
def func():
    pass
123
"""
        },
        execute=True,
    )
    assert reply["content"]["data"]["text/plain"] == "123"

    reply = await shell_msg(
        sock,
        "execute_request",
        {
            "code": """
@time_trigger("once(2019/9/7 13:00)")
@state_trigger("pyscript.var1 == '1'")
@event_trigger("test_event2")
def func():
    pass
321
"""
        },
        execute=True,
    )
    assert reply["content"]["data"]["text/plain"] == "321"

    await shutdown(sock)


async def test_jupyter_kernel_global_ctx_func(hass, caplog):
    """Test Jupyter kernel global_ctx functions."""
    sock, _ = await setup_script(hass, [dt(2020, 7, 1, 11, 0, 0, 0)], "")

    reply = await shell_msg(sock, "execute_request", {"code": "pyscript.get_global_ctx()"}, execute=True)
    assert literal_eval(reply["content"]["data"]["text/plain"]).startswith("jupyter_")

    reply = await shell_msg(sock, "execute_request", {"code": "pyscript.list_global_ctx()"}, execute=True)
    ctx_list = literal_eval(reply["content"]["data"]["text/plain"])
    assert len(ctx_list) == 2
    assert ctx_list[0].startswith("jupyter_")
    assert ctx_list[1] == "file.hello"

    reply = await shell_msg(
        sock, "execute_request", {"code": "pyscript.set_global_ctx('file.hello'); 456"}, execute=True
    )
    assert literal_eval(reply["content"]["data"]["text/plain"]) == 456

    await shutdown(sock)


async def test_jupyter_kernel_stdout(hass, caplog):
    """Test Jupyter kernel stdout."""
    sock, _ = await setup_script(hass, [dt(2020, 7, 1, 11, 0, 0, 0)], "")

    reply = await shell_msg(sock, "execute_request", {"code": "log.info('hello'); 123"}, execute=True)
    assert reply["header"]["msg_type"] == "execute_result"
    assert reply["content"]["data"]["text/plain"] == "123"
    stdout_msg = await get_iopub_msg(sock, "stream")
    assert stdout_msg["content"]["text"] == "hello\n"

    await shutdown(sock)


async def test_jupyter_kernel_no_connection_timeout(hass, caplog):
    """Test Jupyter kernel timeout on no connection."""
    await setup_script(hass, [dt(2020, 7, 1, 11, 0, 0, 0)], "", no_connect=True)

    #
    # There is a race condition waiting for the log message, so we need to poll
    #
    for _ in range(1000):
        if "No connections to session jupyter_" in caplog.text:
            break
        await asyncio.sleep(2e-3)

    assert "No connections to session jupyter_" in caplog.text
