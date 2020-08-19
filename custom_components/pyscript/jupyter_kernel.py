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
import traceback
import uuid

import zmq
import zmq.asyncio

from .const import LOGGER_PATH

_LOGGER = logging.getLogger(LOGGER_PATH + ".jupyter_kernel")

# Globals:

DELIM = b"<IDS|MSG>"

def msg_id():
    """ Return a new uuid for message id """
    return str(uuid.uuid4())

def str_to_bytes(string):
    """Encode a string in bytes."""
    return string.encode('ascii')

def bind(socket, connection, port):
    """Bind a socket."""
    if port <= 0:
        return socket.bind_to_random_port(connection)
    # _LOGGER.debug(f"binding to %s:%s" % (connection, port))
    socket.bind("%s:%s" % (connection, port))
    return port

class KernelBufferingHandler(logging.handlers.BufferingHandler):
    """Memory-based handler for logging; send via stdout queue."""
    def __init__(self, stdout_q):
        super().__init__(0)
        self.stdout_q = stdout_q

    def flush(self):
        pass

    def shouldFlush(self, record):
        try:
            self.stdout_q.put_nowait(self.format(record))
        except asyncio.QueueFull:
            _LOGGER.error("stdout_q unexpectedly full")

##########################################
class Kernel:
    """Define Kernel class."""
    def __init__(self, config, ast_ctx, global_ctx_name, global_ctx_mgr):
        """Initialize a Kernel object."""
        self.config = config.copy()
        self.global_ctx_name = global_ctx_name
        self.global_ctx_mgr = global_ctx_mgr
        self.zmq_ctx = zmq.asyncio.Context()
        self.ast_ctx = ast_ctx

        self.connection = self.config["transport"] + "://" + self.config["ip"]
        self.secure_key = str_to_bytes(self.config["key"])
        self.signature_schemes = {"hmac-sha256": hashlib.sha256}
        self.auth = hmac.HMAC(
            self.secure_key,
            digestmod=self.signature_schemes[self.config["signature_scheme"]]
        )
        self.execution_count = 1
        self.engine_id = str(uuid.uuid4())
        self.shutdown_event = asyncio.Event()

        self.iopub_task = None
        self.control_task = None
        self.stdin_task = None
        self.shell_task = None
        self.heartbeat_task = None
        self.stdout_q_task = None

        self.parent_header = None

        self.iopub_socket = None
        self.control_socket = None
        self.stdin_socket = None
        self.shell_socket = None
        self.hb_write_sock = None
        self.hb_read_sock = None

        self.stdout_q = asyncio.Queue(0)
        self.console = KernelBufferingHandler(self.stdout_q)
        self.console.setLevel(logging.DEBUG)
        # set a format which is just the message
        formatter = logging.Formatter('%(message)s')
        self.console.setFormatter(formatter)

        # match alphanum or "." at end of line
        self.completion_re = re.compile(r'.*?([\w.]*)$')

        # see if line ends in a ":", with optional whitespace and comment
        # note: this doesn't detect if we are inside a quoted string...
        self.colon_end_re = re.compile(r'.*: *(#.*)?$')

        self.shutdown_lock = asyncio.Lock()

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
        msg_frames = wire_msg[delim_idx + 2:]

        def decode(msg):
            return json.loads(msg.decode('ascii'))

        msg = {}
        msg['header']        = decode(msg_frames[0])
        msg['parent_header'] = decode(msg_frames[1])
        msg['metadata']      = decode(msg_frames[2])
        msg['content']       = decode(msg_frames[3])
        check_sig = self.msg_sign(msg_frames)
        if check_sig != m_signature:
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

    async def send(self, stream, msg_type, content=None, parent_header=None, metadata=None, identities=None):
        """Send message to the Jupyter client."""
        header = self.new_header(msg_type)
        if content is None:
            content = {}
        if parent_header is None:
            parent_header = {}
        if metadata is None:
            metadata = {}

        def encode(msg):
            return str_to_bytes(json.dumps(msg))

        msg_lst = [
            encode(header),
            encode(parent_header),
            encode(metadata),
            encode(content),
        ]
        signature = self.msg_sign(msg_lst)
        parts = [DELIM,
                 signature,
                 msg_lst[0],
                 msg_lst[1],
                 msg_lst[2],
                 msg_lst[3]]
        if identities:
            parts = identities + parts
        # _LOGGER.debug("send %s: %s", msg_type, parts)
        await stream.send_multipart(parts)

    async def shell_handler(self, msg):
        """Handle shell messages."""
        identities, msg = self.deserialize_wire_msg(msg)
        # _LOGGER.debug("shell received %s: %s", msg.get('header', {}).get('msg_type', 'UNKNOWN'), msg)

        self.parent_header = msg['header']

        # process request:

        if msg['header']["msg_type"] == "execute_request":
            content = {
                'execution_state': "busy",
            }
            await self.send(self.iopub_socket, 'status', content, parent_header=msg['header'])
            #######################################################################
            content = {
                'execution_count': self.execution_count,
                'code': msg['content']["code"],
            }
            await self.send(self.iopub_socket, 'execute_input', content, parent_header=msg['header'])

            #######################################################################
            code = msg['content']["code"]
            self.ast_ctx.parse(code)
            exc = self.ast_ctx.get_exception_obj()
            if exc is None:
                result = await self.ast_ctx.eval()
                exc = self.ast_ctx.get_exception_obj()
            if exc:
                traceback_mesg = self.ast_ctx.get_exception_long().split("\n")
                metadata = {
                    "dependencies_met": True,
                    "engine": self.engine_id,
                    "status": "error",
                    "started": datetime.datetime.now().isoformat(),
                }
                content = {
                    'execution_count': self.execution_count,
                    'status': 'error',
                    'ename': type(exc).__name__,   # Exception name, as a string
                    'evalue': str(exc),  # Exception value, as a string
                    'traceback': traceback_mesg,
                }
                _LOGGER.debug("Executing '%s' got exception: %s", code, content)
                await self.send(self.shell_socket, 'execute_reply', content, metadata=metadata,
                    parent_header=msg['header'], identities=identities)
                del content["execution_count"], content["status"]
                await self.send(self.iopub_socket, 'error', content, parent_header=msg['header'])
                content = {
                    'execution_state': "idle",
                }
                await self.send(self.iopub_socket, 'status', content, parent_header=msg['header'])
                self.execution_count += 1
                return

            # if True or isinstance(self.ast_ctx.ast, ast.Expr):
            _LOGGER.debug("Executing: '%s' got result %s", code, result)
            if result is not None:
                content = {
                    'execution_count': self.execution_count,
                    'data': {"text/plain": repr(result)},
                    'metadata': {}
                }
                await self.send(self.iopub_socket, 'execute_result', content, parent_header=msg['header'])

            #######################################################################
            content = {
                'execution_state': "idle",
            }
            await self.send(self.iopub_socket, 'status', content, parent_header=msg['header'])
            #######################################################################
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
            await self.send(self.shell_socket, 'execute_reply', content, metadata=metadata,
                parent_header=msg['header'], identities=identities)
            self.execution_count += 1
        elif msg['header']["msg_type"] == "kernel_info_request":
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
                    'mimetype': "",
                    'file_extension': ".py",
                    #'pygments_lexer': "",
                    'codemirror_mode': "",
                    'nbconvert_exporter': "",
                },
                "banner": ""
            }
            await self.send(self.shell_socket, 'kernel_info_reply', content, parent_header=msg['header'], identities=identities)
            content = {
                'execution_state': "idle",
            }
            await self.send(self.iopub_socket, 'status', content, parent_header=msg['header'])
        elif msg['header']["msg_type"] == "complete_request":
            code = msg["content"]["code"]
            posn = msg["content"]["cursor_pos"]
            match = self.completion_re.match(code[0:posn].lower())
            if match:
                root = match[1].lower()
                words = self.ast_ctx.state.completions(root)
                words = words.union(await self.ast_ctx.handler.service_completions(root))
                words = words.union(await self.ast_ctx.handler.func_completions(root))
                words = words.union(self.ast_ctx.completions(root))
            else:
                root = ""
                words = set()
            # _LOGGER.debug(f"complete_request code={code}, posn={posn}, root={root}, words={words}")
            content = {
                "status": "ok",
                "matches": sorted(list(words)),
                "cursor_start": msg["content"]["cursor_pos"] - len(root),
                "cursor_end": msg["content"]["cursor_pos"],
            }
            await self.send(self.shell_socket, 'complete_request', content, parent_header=msg['header'], identities=identities)
        elif msg['header']["msg_type"] == "is_complete_request":
            code = msg['content']["code"]
            self.ast_ctx.parse(code)
            exc = self.ast_ctx.get_exception_obj()

            # determine indent of last line
            indent = 0
            i = code.rfind("\n") 
            if i >= 0:
                while i + 1 < len(code) and code[i+1] == " ":
                    i += 1
                    indent += 1
            if exc is None:
                if indent == 0:
                    content = {
                        # One of 'complete', 'incomplete', 'invalid', 'unknown'
                        "status": 'complete',
                        # If status is 'incomplete', indent should contain the characters to use
                        # to indent the next line. This is only a hint: frontends may ignore it
                        # and use their own autoindentation rules. For other statuses, this
                        # field does not exist.
                        #"indent": str,
                    }
                else:
                    content = {
                        "status": 'incomplete',
                        "indent": " " * indent,
                    }
            else:
                #
                # if the syntax error is right at the end, then we label it incomplete,
                # otherwise it's invalid
                #
                if str(exc).find("EOF while") >= 0:
                    # if error is at ":" then increase indent
                    if hasattr(exc, "lineno"):
                        line = code.split("\n")[exc.lineno-1]
                        if self.colon_end_re.match(line):
                            indent += 4
                    content = {
                        "status": 'incomplete',
                        "indent": " " * indent,
                    }
                else:
                    content = {
                        "status": 'invalid',
                    }
            # _LOGGER.debug(f"is_complete_request code={code}, exc={exc}, content={content}")
            await self.send(self.shell_socket, 'is_complete_reply', content, parent_header=msg['header'], identities=identities)
        elif msg['header']["msg_type"] == "comm_info_request":
            content = {
                "comms": {}
            }
            await self.send(self.shell_socket, 'comm_info_reply', content, parent_header=msg['header'], identities=identities)
        elif msg['header']["msg_type"] == "history_request":
            content = {
                "history": []
            }
            await self.send(self.shell_socket, 'history_reply', content, parent_header=msg['header'], identities=identities)
        else:
            _LOGGER.error("unknown msg_type: %s", msg['header']["msg_type"])


    ##########################################
    # IOPub/Sub:
    # aslo called SubSocketChannel in IPython sources
    async def iopub_listen(self):
        """Task that listens to iopub messages."""
        try:
            self.iopub_socket = self.zmq_ctx.socket(zmq.PUB)  # pylint: disable=no-member
            self.config["iopub_port"] = bind(self.iopub_socket, self.connection, self.config["iopub_port"])
            while 1:
                _ = await self.iopub_socket.recv_multipart()
                # _LOGGER.debug("iopub received %s: %s", msg.get('header', {}).get('msg_type', 'UNKNOWN'), msg)
        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            raise
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("iopub_listen exception %s", err)
            await self.shutdown_lock.acquire()
            asyncio.create_task(self.session_shutdown())

    ##########################################
    # Control:
    async def control_listen(self):
        """Task that listens to control messages."""
        try:
            self.control_socket = self.zmq_ctx.socket(zmq.ROUTER)  # pylint: disable=no-member
            self.config["control_port"] = bind(self.control_socket, self.connection, self.config["control_port"])
            while 1:
                wire_msg = await self.control_socket.recv_multipart()
                _, msg = self.deserialize_wire_msg(wire_msg)
                # _LOGGER.debug("control received %s: %s", msg.get('header', {}).get('msg_type', 'UNKNOWN'), msg)
                # Control message handler:
                if msg['header']["msg_type"] == "shutdown_request":
                    # if msg["content"]["restart"]:
                    #     asyncio.create_task(self.session_restart())
                    # else:
                    await self.shutdown_lock.acquire()
                    asyncio.create_task(self.session_shutdown())
                    return
        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            raise
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("control_listen exception %s", err)
            await self.shutdown_lock.acquire()
            asyncio.create_task(self.session_shutdown())

    ##########################################
    # Stdin:
    async def stdin_listen(self):
        """Task that listens to stdin messages."""
        try:
            self.stdin_socket = self.zmq_ctx.socket(zmq.ROUTER)  # pylint: disable=no-member
            self.config["stdin_port"] = bind(self.stdin_socket, self.connection, self.config["stdin_port"])
            while 1:
                _ = await self.stdin_socket.recv_multipart()
                # _LOGGER.debug("stdin received %s: %s", msg.get('header', {}).get('msg_type', 'UNKNOWN'), msg)
        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            raise
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("stdin_listen exception %s", err)
            await self.shutdown_lock.acquire()
            asyncio.create_task(self.session_shutdown())

    ##########################################
    # Shell:
    async def shell_listen(self):
        """Task that listens to shell messages."""
        try:
            self.shell_socket = self.zmq_ctx.socket(zmq.ROUTER)  # pylint: disable=no-member
            self.config["shell_port"] = bind(self.shell_socket, self.connection, self.config["shell_port"])
            while 1:
                msg = await self.shell_socket.recv_multipart()
                await self.shell_handler(msg)
        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            raise
        except Exception:  # pylint: disable=broad-except
            _LOGGER.error("shell_listen exception %s", traceback.format_exc(-1))
            await self.shutdown_lock.acquire()
            asyncio.create_task(self.session_shutdown())

    ##########################################
    # Heartbeat:
    async def heartbeat_listen(self):
        """Task that listens and responds to heart beat messages."""
        try:
            # _LOGGER.debug(f"heartbeat_listen: connecting to {self.config['ip']}:{self.config['hb_port']}")
            self.hb_read_sock, self.hb_write_sock = await asyncio.open_connection(self.config["ip"], self.config["hb_port"])
            while 1:
                msg = await self.hb_read_sock.read(1024)
                # _LOGGER.debug(f"heartbeat_listen: got {msg}")
                if len(msg) == 0:
                    _LOGGER.error("heartbeat_listen got EOF")
                    await self.shutdown_lock.acquire()
                    asyncio.create_task(self.session_shutdown())
                    return
                self.hb_write_sock.write(msg)
                await self.hb_write_sock.drain()
        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            raise
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("heartbeat_listen exception: %s", err)
            await self.shutdown_lock.acquire()
            asyncio.create_task(self.session_shutdown())

    ##########################################
    # Pass along stdout messages
    async def stdout_q_listen(self):
        """Listen to the stdout queue, and send the messages to the client."""
        while 1:
            try:
                msg = await self.stdout_q.get()
                content = {
                    'name': "stdout",
                    'text': msg + "\n"
                }
                if self.iopub_socket is None:
                    continue
                await self.send(self.iopub_socket, 'stream', content, parent_header=self.parent_header)
            except asyncio.CancelledError:  # pylint: disable=try-except-raise
                raise
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("stdout_q_listen exception: %s", err)
                await self.shutdown_lock.acquire()
                asyncio.create_task(self.session_shutdown())

    async def session_start(self):
        """Shutdown the kernel session."""
        self.ast_ctx.add_logger_handler(self.console)
        # logging.getLogger("homeassistant.components.pyscript.func").addHandler(self.console)
        self.shutdown_event.clear()
        _LOGGER.info("Starting session %s", self.global_ctx_name)
        self.stdout_q_task = asyncio.create_task(self.stdout_q_listen())
        self.iopub_task = asyncio.create_task(self.iopub_listen())
        self.control_task = asyncio.create_task(self.control_listen())
        self.stdin_task = asyncio.create_task(self.stdin_listen())
        self.shell_task = asyncio.create_task(self.shell_listen())
        self.heartbeat_task = asyncio.create_task(self.heartbeat_listen())

    async def session_shutdown(self):
        """Shutdown the kernel session."""
        await self.global_ctx_mgr.delete(self.global_ctx_name)
        self.ast_ctx.remove_logger_handler(self.console)
        # logging.getLogger("homeassistant.components.pyscript.func.").removeHandler(self.console)
        _LOGGER.info("Shutting down session %s", self.global_ctx_name)
        for task in [self.iopub_task, self.control_task, self.stdin_task, self.shell_task, self.heartbeat_task, self.stdout_q_task]:
            if task is None:
                continue
            try:
                task.cancel()
                await task
            except asyncio.CancelledError:  # pylint: disable=try-except-raise
                pass

        for sock in [self.iopub_socket, self.control_socket, self.stdin_socket, self.shell_socket, self.hb_write_sock]:
            if sock is None:
                continue
            try:
                sock.close()
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("socket close exception: %s", err)

        self.iopub_task = None
        self.control_task = None
        self.stdin_task = None
        self.shell_task = None
        self.heartbeat_task = None

        self.iopub_socket = None
        self.control_socket = None
        self.stdin_socket = None
        self.shell_socket = None
        self.hb_write_sock = None
        self.hb_read_sock = None

        self.shutdown_event.set()
        self.shutdown_lock.release()

    async def session_restart(self):
        """Stop and restart the kernel session."""
        await self.session_shutdown()
        await self.session_start()

    async def wait_until_shutdown(self):
        """Wait until the kernel shuts down."""
        await self.shutdown_event.wait()
