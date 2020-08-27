"""Pyscript kernel shim for Jupyter."""

import asyncio
import json
import socket
import sys
import traceback

import requests

#
# Set HASS_URL to the URL of your HASS http interface
#
HASS_URL = "http://localhost:8123"

#
# Set HASS_TOKEN to a long-term access token created via the button
# at the bottom of your user profile page in HASS.
#
HASS_TOKEN = "REPLACE_WITH_THE_LONG_TERM_ACCESS_KEY_FROM_HASS"


def current_task():
    """Return our asyncio current task."""
    try:
        # python >= 3.7
        return asyncio.current_task()
    except AttributeError:
        # python <= 3.6
        return asyncio.tasks.Task.current_task()


class RelayPort:
    """Define the RelayPort class, that does full-duplex forwarding between TCP endpoints."""
    def __init__(self, name, config, debug=False):
        """Initialize a relay port."""
        self.name = name
        self.config = config
        self.debug = debug
        self.ip_host = config["ip"]
        self.client_port = config[name]
        self.client2kernel_task = None
        self.kernel2client_task = None
        self.kernel_connect_task = None
        self.client_server = None

        self.kernel_reader = None
        self.kernel_writer = None

        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\0" * 8)
        sock.bind((self.ip_host, 0))
        self.kernel_port = sock.getsockname()[1]
        sock.close()
        config[name] = self.kernel_port

    async def client_server_start(self, status_q):
        """Start a server that listens for client connections."""
        async def client_connected(reader, writer):
            my_exit_q = asyncio.Queue(0)
            client_reader = reader
            client_writer = writer
            await status_q.put(["task_start", current_task()])

            while True:
                try:
                    kernel_reader, kernel_writer = await asyncio.open_connection(
                        self.ip_host, self.kernel_port
                    )
                    break
                except Exception:  # pylint: disable=broad-except
                    await asyncio.sleep(0.25)

            client2kernel_task = asyncio.create_task(
                self.forward_data_task("c2k", client_reader, kernel_writer, my_exit_q)
            )
            kernel2client_task = asyncio.create_task(
                self.forward_data_task("k2c", kernel_reader, client_writer, my_exit_q)
            )
            for task in [client2kernel_task, kernel2client_task]:
                await status_q.put(["task_start", task])

            exit_status = await my_exit_q.get()
            for task in [client2kernel_task, kernel2client_task]:
                try:
                    task.cancel()
                    await task
                except asyncio.CancelledError:
                    pass
            for sock in [client_writer, kernel_writer]:
                sock.close()
            for task in [current_task(), client2kernel_task, kernel2client_task]:
                await status_q.put(["task_end", task])
            if exit_status:
                await status_q.put(["exit", exit_status])

        self.client_server = await asyncio.start_server(
            client_connected, self.ip_host, self.client_port
        )

    async def client_server_stop(self):
        """Stop the server waiting for client connections."""
        if self.client_server:
            self.client_server.close()
            self.client_server = None

    async def forward_data_task(self, dir_str, reader, writer, exit_q):
        """Forward data from one side to the other."""
        try:
            while True:
                data = await reader.read(1024)
                if len(data) == 0:
                    await exit_q.put(0)
                    return
                if self.debug:
                    print(f"{self.name} {dir_str}: {data} ### {data.hex(' ')}")
                writer.write(data)
                await writer.drain()
        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            raise
        except Exception as err:  # pylint: disable=broad-except
            print(
                f"{sys.argv[0]}: {self.name} {dir_str} got exception {err}; {traceback.format_exc(0)}"
            )
            await exit_q.put(1)
            return


#
# Call the service pyscript/jupyter_kernel_start.  We can't immediately
# exit since Jupyter thinks the kernel has stopped.  We sit in the
# middle of the heartbeat loop so we know when the kernel is stopped.
#
async def kernel_run(config_filename):
    """Start a new pyscript kernel."""
    url = HASS_URL + "/api/services/pyscript/jupyter_kernel_start"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + HASS_TOKEN,
    }
    with open(config_filename, "r") as fp:
        config = json.load(fp)
    ip_host = config["ip"]

    #
    # We act as a tcp relay on all the links between the Jupyter client and pyscript kernel.
    #
    relay_ports = {
        "hb_port": RelayPort("hb_port", config),
        "stdin_port": RelayPort("stdin_port", config),
        "shell_port": RelayPort("shell_port", config),
        "iopub_port": RelayPort("iopub_port", config),
        "control_port": RelayPort("control_port", config),
    }

    #
    # There is a potential race condition if this script exits and Jupyter restarts
    # it before HASS has shutdown the old session.  So before we issue the service
    # call we check if the iopub_port is free.  If not we wait until it is.
    #
    async def null_server(reader, writer):
        writer.close()

    while True:
        try:
            iopub_server = await asyncio.start_server(
                null_server, ip_host, config["iopub_port"]
            )
            iopub_server.close()
            break
        except OSError:
            await asyncio.sleep(0.25)

    #
    # now call the service
    #
    try:
        requests.request("POST", url, headers=headers, data=json.dumps(config))
    except requests.exceptions.ConnectionError as err:
        print(f"{sys.argv[0]}: unable to connect to {url} ({err})")
        sys.exit(1)
    except requests.exceptions.Timeout as err:
        print(f"{sys.argv[0]}: timeout connecting to {url} ({err})")
        sys.exit(1)
    except Exception as err:  # pylint: disable=broad-except
        print(f"{sys.argv[0]}: got error {err} (url={url})")
        sys.exit(1)

    status_q = asyncio.Queue(0)

    # connect to kernel and client
    for port in relay_ports.values():
        await port.client_server_start(status_q)

    tasks = set()
    task_cnt_max = 0
    while True:
        status = await status_q.get()
        if status[0] == "task_start":
            tasks.add(status[1])
            task_cnt_max = max(len(tasks), task_cnt_max)
        elif status[0] == "task_end":
            tasks.discard(status[1])
            if len(tasks) == 0 and task_cnt_max >= 10:
                exit_status = 0
                break
        elif status[0] == "exit":
            exit_status = status[1]
            break

    for port in relay_ports.values():
        await port.client_server_stop()
    for task in tasks:
        try:
            task.cancel()
            await task
        except asyncio.CancelledError:
            pass
    sys.exit(exit_status)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} configFile.json")
        sys.exit(1)
    asyncio.run(kernel_run(sys.argv[1]))
