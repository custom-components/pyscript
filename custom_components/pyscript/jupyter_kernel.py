"""Pyscript Jupyter kernel."""

#
# Based on simple_kernel.py by Doug Blank <doug.blank@gmail.com>
#   https://github.com/dsblank/simple_kernel
#   license: public domain
#   Thanks Doug!
#

import asyncio
import datetime
import hashlib
import hmac
import json
import logging
import logging.handlers
import re
from struct import pack, unpack
import traceback
import uuid

from .const import LOGGER_PATH
from .function import Function
from .global_ctx import GlobalContextMgr
from .state import State

_LOGGER = logging.getLogger(LOGGER_PATH + ".jupyter_kernel")

# Globals:

DELIM = b"<IDS|MSG>"


def msg_id():
    """Return a new uuid for message id."""
    return str(uuid.uuid4())


def str_to_bytes(string):
    """Encode a string in bytes."""
    return string.encode("utf-8")


class KernelBufferingHandler(logging.handlers.BufferingHandler):
    """Memory-based handler for logging; send via stdout queue."""

    def __init__(self, housekeep_q):
        """Initialize KernelBufferingHandler instance."""
        super().__init__(0)
        self.housekeep_q = housekeep_q

    def flush(self):
        """Flush is a no-op."""

    def shouldFlush(self, record):
        """Write the buffer to the housekeeping queue."""
        try:
            self.housekeep_q.put_nowait(["stdout", self.format(record)])
        except asyncio.QueueFull:
            _LOGGER.error("housekeep_q unexpectedly full")


################################################################
class ZmqSocket:
    """Defines a minimal implementation of a small subset of ZMQ."""

    #
    # This allows pyscript to work with Jupyter without the real zmq
    # and pyzmq packages, which might not be available or easy to
    # install on the wide set of HASS platforms.
    #
    def __init__(self, reader, writer, sock_type):
        """Initialize a ZMQ socket with the given type and reader/writer streams."""
        self.writer = writer
        self.reader = reader
        self.type = sock_type

    async def read_bytes(self, num_bytes):
        """Read bytes from ZMQ socket."""
        data = b""
        while len(data) < num_bytes:
            new_data = await self.reader.read(num_bytes - len(data))
            if len(new_data) == 0:
                raise EOFError
            data += new_data
        return data

    async def write_bytes(self, raw_msg):
        """Write bytes to ZMQ socket."""
        self.writer.write(raw_msg)
        await self.writer.drain()

    async def handshake(self):
        """Do initial greeting handshake on a new ZMQ connection."""
        await self.write_bytes(b"\xff\x00\x00\x00\x00\x00\x00\x00\x01\x7f")
        _ = await self.read_bytes(10)
        # _LOGGER.debug(f"handshake: got initial greeting {greeting}")
        await self.write_bytes(b"\x03")
        _ = await self.read_bytes(1)
        await self.write_bytes(b"\x00" + "NULL".encode() + b"\x00" * 16 + b"\x00" + b"\x00" * 31)
        _ = await self.read_bytes(53)
        # _LOGGER.debug(f"handshake: got rest of greeting {greeting}")
        params = [["Socket-Type", self.type]]
        if self.type == "ROUTER":
            params.append(["Identity", ""])
        await self.send_cmd("READY", params)

    async def recv(self, multipart=False):
        """Receive a message from ZMQ socket."""
        parts = []
        while 1:
            cmd = (await self.read_bytes(1))[0]
            if cmd & 0x2:
                msg_len = unpack(">Q", await self.read_bytes(8))[0]
            else:
                msg_len = (await self.read_bytes(1))[0]
            msg_body = await self.read_bytes(msg_len)
            if cmd & 0x4:
                # _LOGGER.debug(f"recv: got cmd {msg_body}")
                cmd_len = msg_body[0]
                cmd = msg_body[1 : cmd_len + 1]
                msg_body = msg_body[cmd_len + 1 :]
                params = []
                while len(msg_body) > 0:
                    param_len = msg_body[0]
                    param = msg_body[1 : param_len + 1]
                    msg_body = msg_body[param_len + 1 :]
                    value_len = unpack(">L", msg_body[0:4])[0]
                    value = msg_body[4 : 4 + value_len]
                    msg_body = msg_body[4 + value_len :]
                    params.append([param, value])
                # _LOGGER.debug(f"recv: got cmd={cmd}, params={params}")
            else:
                parts.append(msg_body)
                if cmd in (0x0, 0x2):
                    # _LOGGER.debug(f"recv: got msg {parts}")
                    if not multipart:
                        return b"".join(parts)

                    return parts

    async def recv_multipart(self):
        """Receive a multipart message from ZMQ socket."""
        return await self.recv(multipart=True)

    async def send_cmd(self, cmd, params):
        """Send a command over ZMQ socket."""
        raw_msg = bytearray([len(cmd)]) + cmd.encode()
        for param in params:
            raw_msg += bytearray([len(param[0])]) + param[0].encode()
            raw_msg += pack(">L", len(param[1])) + param[1].encode()
        len_msg = len(raw_msg)
        if len_msg <= 255:
            raw_msg = bytearray([0x4, len_msg]) + raw_msg
        else:
            raw_msg = bytearray([0x6]) + pack(">Q", len_msg) + raw_msg
        # _LOGGER.debug(f"send_cmd: sending {raw_msg}")
        await self.write_bytes(raw_msg)

    async def send(self, msg):
        """Send a message over ZMQ socket."""
        len_msg = len(msg)
        if len_msg <= 255:
            raw_msg = bytearray([0x1, 0x0, 0x0, len_msg]) + msg
        else:
            raw_msg = bytearray([0x1, 0x0, 0x2]) + pack(">Q", len_msg) + msg
        # _LOGGER.debug(f"send: sending {raw_msg}")
        await self.write_bytes(raw_msg)

    async def send_multipart(self, parts):
        """Send multipart messages over ZMQ socket."""
        raw_msg = b""
        for i, part in enumerate(parts):
            len_part = len(part)
            cmd = 0x1 if i < len(parts) - 1 else 0x0
            if len_part <= 255:
                raw_msg += bytearray([cmd, len_part]) + part
            else:
                raw_msg += bytearray([cmd + 2]) + pack(">Q", len_part) + part
        # _LOGGER.debug(f"send_multipart: sending {raw_msg}")
        await self.write_bytes(raw_msg)

    def close(self):
        """Close the ZMQ socket."""
        self.writer.close()


##########################################
class Kernel:
    """Define a Jupyter Kernel class."""

    def __init__(self, config, ast_ctx, global_ctx, global_ctx_name):
        """Initialize a Kernel object, one instance per session."""
        self.config = config.copy()
        self.global_ctx = global_ctx
        self.global_ctx_name = global_ctx_name
        self.ast_ctx = ast_ctx

        self.secure_key = str_to_bytes(self.config["key"])
        self.no_connect_timeout = self.config.get("no_connect_timeout", 30)
        self.signature_schemes = {"hmac-sha256": hashlib.sha256}
        self.auth = hmac.HMAC(
            self.secure_key,
            digestmod=self.signature_schemes[self.config["signature_scheme"]],
        )
        self.execution_count = 1
        self.engine_id = str(uuid.uuid4())

        self.heartbeat_server = None
        self.iopub_server = None
        self.control_server = None
        self.stdin_server = None
        self.shell_server = None

        self.heartbeat_port = None
        self.iopub_port = None
        self.control_port = None
        self.stdin_port = None
        self.shell_port = None
        # this should probably be a configuration parameter
        self.avail_port = 50321

        # there can be multiple iopub subscribers, with corresponding tasks
        self.iopub_socket = set()

        self.tasks = {}
        self.task_cnt = 0
        self.task_cnt_max = 0

        self.session_cleanup_callback = None

        self.housekeep_q = asyncio.Queue(0)

        self.parent_header = None

        #
        # we create a logging handler so that output from the log functions
        # gets delivered back to Jupyter as stdout
        #
        self.console = KernelBufferingHandler(self.housekeep_q)
        self.console.setLevel(logging.DEBUG)
        # set a format which is just the message
        formatter = logging.Formatter("%(message)s")
        self.console.setFormatter(formatter)

        # match alphanum or "." at end of line
        self.completion_re = re.compile(r".*?([\w.]*)$", re.DOTALL)

        # see if line ends in a ":", with optional whitespace and comment
        # note: this doesn't detect if we are inside a quoted string...
        self.colon_end_re = re.compile(r".*: *(#.*)?$")

    def msg_sign(self, msg_lst):
        """Sign a message with a secure signature."""
        auth_hmac = self.auth.copy()
        for msg in msg_lst:
            auth_hmac.update(msg)
        return str_to_bytes(auth_hmac.hexdigest())

    def deserialize_wire_msg(self, wire_msg):
        """Split the routing prefix and message frames from a message on the wire."""
        delim_idx = wire_msg.index(DELIM)
        identities = wire_msg[:delim_idx]
        m_signature = wire_msg[delim_idx + 1]
        msg_frames = wire_msg[delim_idx + 2 :]

        def decode(msg):
            return json.loads(msg.decode("utf-8"))

        msg = {}
        msg["header"] = decode(msg_frames[0])
        msg["parent_header"] = decode(msg_frames[1])
        msg["metadata"] = decode(msg_frames[2])
        msg["content"] = decode(msg_frames[3])
        check_sig = self.msg_sign(msg_frames)
        if check_sig != m_signature:
            _LOGGER.error(
                "signature mismatch: check_sig=%s, m_signature=%s, wire_msg=%s",
                check_sig,
                m_signature,
                wire_msg,
            )
            raise ValueError("Signatures do not match")

        return identities, msg

    def new_header(self, msg_type):
        """Make a new header."""
        return {
            "date": datetime.datetime.now().isoformat(),
            "msg_id": msg_id(),
            "username": "kernel",
            "session": self.engine_id,
            "msg_type": msg_type,
            "version": "5.3",
        }

    async def send(
        self,
        stream,
        msg_type,
        content=None,
        parent_header=None,
        metadata=None,
        identities=None,
    ):
        """Send message to the Jupyter client."""
        header = self.new_header(msg_type)

        def encode(msg):
            return str_to_bytes(json.dumps(msg))

        msg_lst = [
            encode(header),
            encode(parent_header if parent_header else {}),
            encode(metadata if metadata else {}),
            encode(content if content else {}),
        ]
        signature = self.msg_sign(msg_lst)
        parts = [DELIM, signature, msg_lst[0], msg_lst[1], msg_lst[2], msg_lst[3]]
        if identities:
            parts = identities + parts
        if stream:
            # _LOGGER.debug("send %s: %s", msg_type, parts)
            for this_stream in stream if isinstance(stream, set) else {stream}:
                await this_stream.send_multipart(parts)

    async def shell_handler(self, shell_socket, wire_msg):
        """Handle shell messages."""

        identities, msg = self.deserialize_wire_msg(wire_msg)
        # _LOGGER.debug("shell received %s: %s", msg.get('header', {}).get('msg_type', 'UNKNOWN'), msg)
        self.parent_header = msg["header"]

        content = {
            "execution_state": "busy",
        }
        await self.send(self.iopub_socket, "status", content, parent_header=msg["header"])

        if msg["header"]["msg_type"] == "execute_request":

            content = {
                "execution_count": self.execution_count,
                "code": msg["content"]["code"],
            }
            await self.send(self.iopub_socket, "execute_input", content, parent_header=msg["header"])
            result = None

            code = msg["content"]["code"]
            #
            # replace VSCode initialization code, which depend on iPython % extensions
            #
            if code.startswith("%config "):
                code = "None"
            if code.startswith("_rwho_ls = %who_ls"):
                code = "print([])"

            self.global_ctx.set_auto_start(False)
            self.ast_ctx.parse(code)
            exc = self.ast_ctx.get_exception_obj()
            if exc is None:
                result = await self.ast_ctx.eval()
                exc = self.ast_ctx.get_exception_obj()
            await Function.waiter_sync()
            self.global_ctx.set_auto_start(True)
            self.global_ctx.start()
            if exc:
                traceback_mesg = self.ast_ctx.get_exception_long().split("\n")

                metadata = {
                    "dependencies_met": True,
                    "engine": self.engine_id,
                    "status": "error",
                    "started": datetime.datetime.now().isoformat(),
                }
                content = {
                    "execution_count": self.execution_count,
                    "status": "error",
                    "ename": type(exc).__name__,  # Exception name, as a string
                    "evalue": str(exc),  # Exception value, as a string
                    "traceback": traceback_mesg,
                }
                _LOGGER.debug("Executing '%s' got exception: %s", code, content)
                await self.send(
                    shell_socket,
                    "execute_reply",
                    content,
                    metadata=metadata,
                    parent_header=msg["header"],
                    identities=identities,
                )
                del content["execution_count"], content["status"]
                await self.send(self.iopub_socket, "error", content, parent_header=msg["header"])

                content = {
                    "execution_state": "idle",
                }
                await self.send(self.iopub_socket, "status", content, parent_header=msg["header"])
                if msg["content"].get("store_history", True):
                    self.execution_count += 1
                return

            # if True or isinstance(self.ast_ctx.ast, ast.Expr):
            _LOGGER.debug("Executing: '%s' got result %s", code, result)
            if result is not None:
                content = {
                    "execution_count": self.execution_count,
                    "data": {"text/plain": repr(result)},
                    "metadata": {},
                }
                await self.send(
                    self.iopub_socket,
                    "execute_result",
                    content,
                    parent_header=msg["header"],
                )

            metadata = {
                "dependencies_met": True,
                "engine": self.engine_id,
                "status": "ok",
                "started": datetime.datetime.now().isoformat(),
            }
            content = {
                "status": "ok",
                "execution_count": self.execution_count,
                "user_variables": {},
                "payload": [],
                "user_expressions": {},
            }
            await self.send(
                shell_socket,
                "execute_reply",
                content,
                metadata=metadata,
                parent_header=msg["header"],
                identities=identities,
            )
            if msg["content"].get("store_history", True):
                self.execution_count += 1

            #
            # Make sure stdout gets sent before set report execution_state idle on iopub,
            # otherwise VSCode doesn't display stdout.  We do a handshake with the
            # housekeep task to ensure any queued messages get processed.
            #
            handshake_q = asyncio.Queue(0)
            await self.housekeep_q.put(["handshake", handshake_q, 0])
            await handshake_q.get()

        elif msg["header"]["msg_type"] == "kernel_info_request":
            content = {
                "protocol_version": "5.3",
                "ipython_version": [1, 1, 0, ""],
                "language_version": [0, 0, 1],
                "language": "python",
                "implementation": "python",
                "implementation_version": "3.7",
                "language_info": {
                    "name": "python",
                    "version": "1.0",
                    "mimetype": "",
                    "file_extension": ".py",
                    "codemirror_mode": "",
                    "nbconvert_exporter": "",
                },
                "banner": "",
            }
            await self.send(
                shell_socket,
                "kernel_info_reply",
                content,
                parent_header=msg["header"],
                identities=identities,
            )

        elif msg["header"]["msg_type"] == "complete_request":
            root = ""
            words = set()
            code = msg["content"]["code"]
            posn = msg["content"]["cursor_pos"]
            match = self.completion_re.match(code[0:posn].lower())
            if match:
                root = match[1].lower()
                words = State.completions(root)
                words = words.union(await Function.service_completions(root))
                words = words.union(await Function.func_completions(root))
                words = words.union(self.ast_ctx.completions(root))
            # _LOGGER.debug(f"complete_request code={code}, posn={posn}, root={root}, words={words}")
            content = {
                "status": "ok",
                "matches": sorted(list(words)),
                "cursor_start": msg["content"]["cursor_pos"] - len(root),
                "cursor_end": msg["content"]["cursor_pos"],
                "metadata": {},
            }
            await self.send(
                shell_socket,
                "complete_reply",
                content,
                parent_header=msg["header"],
                identities=identities,
            )

        elif msg["header"]["msg_type"] == "is_complete_request":
            code = msg["content"]["code"]
            self.ast_ctx.parse(code)
            exc = self.ast_ctx.get_exception_obj()

            # determine indent of last line
            indent = 0
            i = code.rfind("\n")
            if i >= 0:
                while i + 1 < len(code) and code[i + 1] == " ":
                    i += 1
                    indent += 1
            if exc is None:
                if indent == 0:
                    content = {
                        # One of 'complete', 'incomplete', 'invalid', 'unknown'
                        "status": "complete",
                        # If status is 'incomplete', indent should contain the characters to use
                        # to indent the next line. This is only a hint: frontends may ignore it
                        # and use their own autoindentation rules. For other statuses, this
                        # field does not exist.
                        # "indent": str,
                    }
                else:
                    content = {
                        "status": "incomplete",
                        "indent": " " * indent,
                    }
            else:
                #
                # if the syntax error is right at the end, then we label it incomplete,
                # otherwise it's invalid
                #
                if "EOF while" in str(exc) or "expected an indented block" in str(exc):
                    # if error is at ":" then increase indent
                    if hasattr(exc, "lineno"):
                        line = code.split("\n")[exc.lineno - 1]
                        if self.colon_end_re.match(line):
                            indent += 4
                    content = {
                        "status": "incomplete",
                        "indent": " " * indent,
                    }
                else:
                    content = {
                        "status": "invalid",
                    }
            # _LOGGER.debug(f"is_complete_request code={code}, exc={exc}, content={content}")
            await self.send(
                shell_socket,
                "is_complete_reply",
                content,
                parent_header=msg["header"],
                identities=identities,
            )

        elif msg["header"]["msg_type"] == "comm_info_request":
            content = {"comms": {}}
            await self.send(
                shell_socket,
                "comm_info_reply",
                content,
                parent_header=msg["header"],
                identities=identities,
            )

        elif msg["header"]["msg_type"] == "history_request":
            content = {"history": []}
            await self.send(
                shell_socket,
                "history_reply",
                content,
                parent_header=msg["header"],
                identities=identities,
            )

        elif msg["header"]["msg_type"] in {"comm_open", "comm_msg", "comm_close"}:
            # _LOGGER.debug(f"ignore {msg['header']['msg_type']} message ")
            ...
        else:
            _LOGGER.error("unknown msg_type: %s", msg["header"]["msg_type"])

        content = {
            "execution_state": "idle",
        }
        await self.send(self.iopub_socket, "status", content, parent_header=msg["header"])

    async def control_listen(self, reader, writer):
        """Task that listens to control messages."""
        try:
            _LOGGER.debug("control_listen connected")
            await self.housekeep_q.put(["register", "control", asyncio.current_task()])
            control_socket = ZmqSocket(reader, writer, "ROUTER")
            await control_socket.handshake()
            while 1:
                wire_msg = await control_socket.recv_multipart()
                identities, msg = self.deserialize_wire_msg(wire_msg)
                # _LOGGER.debug("control received %s: %s", msg.get('header', {}).get('msg_type', 'UNKNOWN'), msg)
                if msg["header"]["msg_type"] == "shutdown_request":
                    content = {
                        "restart": False,
                    }
                    await self.send(
                        control_socket,
                        "shutdown_reply",
                        content,
                        parent_header=msg["header"],
                        identities=identities,
                    )
                    await self.housekeep_q.put(["shutdown"])
        except asyncio.CancelledError:
            raise
        except (EOFError, ConnectionResetError):
            _LOGGER.debug("control_listen got eof")
            await self.housekeep_q.put(["unregister", "control", asyncio.current_task()])
            control_socket.close()
        except Exception as err:
            _LOGGER.error("control_listen exception %s", err)
            await self.housekeep_q.put(["shutdown"])

    async def stdin_listen(self, reader, writer):
        """Task that listens to stdin messages."""
        try:
            _LOGGER.debug("stdin_listen connected")
            await self.housekeep_q.put(["register", "stdin", asyncio.current_task()])
            stdin_socket = ZmqSocket(reader, writer, "ROUTER")
            await stdin_socket.handshake()
            while 1:
                _ = await stdin_socket.recv_multipart()
                # _LOGGER.debug("stdin_listen received %s", _)
        except asyncio.CancelledError:
            raise
        except (EOFError, ConnectionResetError):
            _LOGGER.debug("stdin_listen got eof")
            await self.housekeep_q.put(["unregister", "stdin", asyncio.current_task()])
            stdin_socket.close()
        except Exception:
            _LOGGER.error("stdin_listen exception %s", traceback.format_exc(-1))
            await self.housekeep_q.put(["shutdown"])

    async def shell_listen(self, reader, writer):
        """Task that listens to shell messages."""
        try:
            _LOGGER.debug("shell_listen connected")
            await self.housekeep_q.put(["register", "shell", asyncio.current_task()])
            shell_socket = ZmqSocket(reader, writer, "ROUTER")
            await shell_socket.handshake()
            while 1:
                msg = await shell_socket.recv_multipart()
                await self.shell_handler(shell_socket, msg)
        except asyncio.CancelledError:
            shell_socket.close()
            raise
        except (EOFError, ConnectionResetError):
            _LOGGER.debug("shell_listen got eof")
            await self.housekeep_q.put(["unregister", "shell", asyncio.current_task()])
            shell_socket.close()
        except Exception:
            _LOGGER.error("shell_listen exception %s", traceback.format_exc(-1))
            await self.housekeep_q.put(["shutdown"])

    async def heartbeat_listen(self, reader, writer):
        """Task that listens and responds to heart beat messages."""
        try:
            _LOGGER.debug("heartbeat_listen connected")
            await self.housekeep_q.put(["register", "heartbeat", asyncio.current_task()])
            heartbeat_socket = ZmqSocket(reader, writer, "REP")
            await heartbeat_socket.handshake()
            while 1:
                msg = await heartbeat_socket.recv()
                # _LOGGER.debug("heartbeat_listen: got %s", msg)
                await heartbeat_socket.send(msg)
        except asyncio.CancelledError:
            raise
        except (EOFError, ConnectionResetError):
            _LOGGER.debug("heartbeat_listen got eof")
            await self.housekeep_q.put(["unregister", "heartbeat", asyncio.current_task()])
            heartbeat_socket.close()
        except Exception:
            _LOGGER.error("heartbeat_listen exception: %s", traceback.format_exc(-1))
            await self.housekeep_q.put(["shutdown"])

    async def iopub_listen(self, reader, writer):
        """Task that listens to iopub messages."""
        try:
            _LOGGER.debug("iopub_listen connected")
            await self.housekeep_q.put(["register", "iopub", asyncio.current_task()])
            iopub_socket = ZmqSocket(reader, writer, "PUB")
            await iopub_socket.handshake()
            self.iopub_socket.add(iopub_socket)
            while 1:
                _ = await iopub_socket.recv_multipart()
                # _LOGGER.debug("iopub received %s", _)
        except asyncio.CancelledError:
            raise
        except (EOFError, ConnectionResetError):
            await self.housekeep_q.put(["unregister", "iopub", asyncio.current_task()])
            iopub_socket.close()
            self.iopub_socket.discard(iopub_socket)
            _LOGGER.debug("iopub_listen got eof")
        except Exception:
            _LOGGER.error("iopub_listen exception %s", traceback.format_exc(-1))
            await self.housekeep_q.put(["shutdown"])

    async def housekeep_run(self):
        """Housekeeping, including closing servers after startup, and doing orderly shutdown."""
        while True:
            try:
                msg = await self.housekeep_q.get()
                if msg[0] == "stdout":
                    content = {"name": "stdout", "text": msg[1] + "\n"}
                    if self.iopub_socket:
                        await self.send(
                            self.iopub_socket,
                            "stream",
                            content,
                            parent_header=self.parent_header,
                            identities=[b"stream.stdout"],
                        )
                elif msg[0] == "handshake":
                    await msg[1].put(msg[2])
                elif msg[0] == "register":
                    if msg[1] not in self.tasks:
                        self.tasks[msg[1]] = set()
                    self.tasks[msg[1]].add(msg[2])
                    self.task_cnt += 1
                    self.task_cnt_max = max(self.task_cnt_max, self.task_cnt)
                    #
                    # now a couple of things are connected, call the session_cleanup_callback
                    #
                    if self.task_cnt > 1 and self.session_cleanup_callback:
                        self.session_cleanup_callback()
                        self.session_cleanup_callback = None
                elif msg[0] == "unregister":
                    if msg[1] in self.tasks:
                        self.tasks[msg[1]].discard(msg[2])
                    self.task_cnt -= 1
                    #
                    # if there are no connection tasks left, then shutdown the kernel
                    #
                    if self.task_cnt == 0 and self.task_cnt_max >= 4:
                        asyncio.create_task(self.session_shutdown())
                        await asyncio.sleep(10000)
                elif msg[0] == "shutdown":
                    asyncio.create_task(self.session_shutdown())
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.error("housekeep task exception: %s", traceback.format_exc(-1))

    async def startup_timeout(self):
        """Shut down the session if nothing connects after 30 seconds."""
        await self.housekeep_q.put(["register", "startup_timeout", asyncio.current_task()])
        await asyncio.sleep(self.no_connect_timeout)
        if self.task_cnt_max <= 1:
            #
            # nothing started other than us, so shut down the session
            #
            _LOGGER.error("No connections to session %s; shutting down", self.global_ctx_name)
            if self.session_cleanup_callback:
                self.session_cleanup_callback()
                self.session_cleanup_callback = None
            await self.housekeep_q.put(["shutdown"])
        await self.housekeep_q.put(["unregister", "startup_timeout", asyncio.current_task()])

    async def start_one_server(self, callback):
        """Start a server by finding an available port."""
        first_port = self.avail_port
        for _ in range(2048):
            try:
                server = await asyncio.start_server(callback, "0.0.0.0", self.avail_port)
                return server, self.avail_port
            except OSError:
                self.avail_port += 1
        _LOGGER.error(
            "unable to find an available port from %d to %d",
            first_port,
            self.avail_port - 1,
        )
        return None, None

    def get_ports(self):
        """Return a dict of the port numbers this kernel session is listening to."""
        return {
            "iopub_port": self.iopub_port,
            "hb_port": self.heartbeat_port,
            "control_port": self.control_port,
            "stdin_port": self.stdin_port,
            "shell_port": self.shell_port,
        }

    def set_session_cleanup_callback(self, callback):
        """Set a cleanup callback which is called right after the session has started."""
        self.session_cleanup_callback = callback

    async def session_start(self):
        """Start the kernel session."""
        self.ast_ctx.add_logger_handler(self.console)
        _LOGGER.info("Starting session %s", self.global_ctx_name)

        self.tasks["housekeep"] = {asyncio.create_task(self.housekeep_run())}
        self.tasks["startup_timeout"] = {asyncio.create_task(self.startup_timeout())}

        self.iopub_server, self.iopub_port = await self.start_one_server(self.iopub_listen)
        self.heartbeat_server, self.heartbeat_port = await self.start_one_server(self.heartbeat_listen)
        self.control_server, self.control_port = await self.start_one_server(self.control_listen)
        self.stdin_server, self.stdin_port = await self.start_one_server(self.stdin_listen)
        self.shell_server, self.shell_port = await self.start_one_server(self.shell_listen)

        #
        # For debugging, can use the real ZMQ library instead on certain sockets; comment out
        # the corresponding asyncio.start_server() call above if you enable the ZMQ-based
        # functions here.  You can then turn of verbosity level 4 (-vvvv) in hass_pyscript_kernel.py
        # to see all the byte data in case you need to debug the simple ZMQ implementation here.
        # The two most important zmq functions are shown below.
        #
        #  import zmq
        #  import zmq.asyncio
        #
        #  def zmq_bind(socket, connection, port):
        #      """Bind a socket."""
        #      if port <= 0:
        #          return socket.bind_to_random_port(connection)
        #      # _LOGGER.debug(f"binding to %s:%s" % (connection, port))
        #      socket.bind("%s:%s" % (connection, port))
        #      return port
        #
        #  zmq_ctx = zmq.asyncio.Context()
        #
        #  ##########################################
        #  # Shell using real ZMQ for debugging:
        #  async def shell_listen_zmq():
        #      """Task that listens to shell messages using ZMQ."""
        #      try:
        #          _LOGGER.debug("shell_listen_zmq connected")
        #          connection = self.config["transport"] + "://" + self.config["ip"]
        #          shell_socket = zmq_ctx.socket(zmq.ROUTER)
        #          self.shell_port = zmq_bind(shell_socket, connection, -1)
        #          _LOGGER.debug("shell_listen_zmq connected")
        #          while 1:
        #              msg = await shell_socket.recv_multipart()
        #              await self.shell_handler(shell_socket, msg)
        #      except asyncio.CancelledError:
        #          raise
        #      except Exception:
        #          _LOGGER.error("shell_listen exception %s", traceback.format_exc(-1))
        #          await self.housekeep_q.put(["shutdown"])
        #
        #  ##########################################
        #  # IOPub using real ZMQ for debugging:
        #  # IOPub/Sub:
        #  async def iopub_listen_zmq():
        #      """Task that listens to iopub messages using ZMQ."""
        #      try:
        #          _LOGGER.debug("iopub_listen_zmq connected")
        #          connection = self.config["transport"] + "://" + self.config["ip"]
        #          iopub_socket = zmq_ctx.socket(zmq.PUB)
        #          self.iopub_port = zmq_bind(self.iopub_socket, connection, -1)
        #          self.iopub_socket.add(iopub_socket)
        #          while 1:
        #              wire_msg = await iopub_socket.recv_multipart()
        #              _LOGGER.debug("iopub received %s", wire_msg)
        #      except asyncio.CancelledError:
        #          raise
        #      except EOFError:
        #          await self.housekeep_q.put(["shutdown"])
        #          _LOGGER.debug("iopub_listen got eof")
        #      except Exception as err:
        #          _LOGGER.error("iopub_listen exception %s", err)
        #          await self.housekeep_q.put(["shutdown"])
        #
        # self.tasks["shell"] = {asyncio.create_task(shell_listen_zmq())}
        # self.tasks["iopub"] = {asyncio.create_task(iopub_listen_zmq())}
        #

    async def session_shutdown(self):
        """Shutdown the kernel session."""
        if not self.iopub_server:
            # already shutdown, so quit
            return
        GlobalContextMgr.delete(self.global_ctx_name)
        self.ast_ctx.remove_logger_handler(self.console)
        # logging.getLogger("homeassistant.components.pyscript.func.").removeHandler(self.console)
        _LOGGER.info("Shutting down session %s", self.global_ctx_name)

        for server in [
            self.heartbeat_server,
            self.control_server,
            self.stdin_server,
            self.shell_server,
            self.iopub_server,
        ]:
            if server:
                server.close()
        self.heartbeat_server = None
        self.iopub_server = None
        self.control_server = None
        self.stdin_server = None
        self.shell_server = None

        for task_set in self.tasks.values():
            for task in task_set:
                try:
                    task.cancel()
                    await task
                except asyncio.CancelledError:
                    pass
        self.tasks = []

        for sock in self.iopub_socket:
            try:
                sock.close()
            except Exception as err:
                _LOGGER.error("iopub socket close exception: %s", err)

        self.iopub_socket = set()
