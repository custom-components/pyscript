"""Pyscript kernel shim for Jupyter."""

import asyncio
import json
import socket
import sys
import traceback

import requests
import zmq
import zmq.asyncio

#
# Set HASS_URL to the URL of your HASS http interface
#
HASS_URL = "http://localhost:8123"

#
# Set HASS_TOKEN to a long-term access token created via the button
# at the bottom of your user profile page in HASS.
#
HASS_TOKEN = "REPLACE_WITH_THE_LONG_TERM_ACCESS_KEY_FROM_HASS"


def zmq_port_url(transport, ip, port):
    if transport == "tcp":
        return f"tcp://{ip}:{port}"
    else:
        return f"{transport}://{ip}-{port}"


#
# Call the service pyscript/jupyter_kernel_start.  We can't immediately
# exit since Jupyter thinks the kernel has stopped.  We sit in the
# middle of the heartbeat loop so we know when the kernel is stopped.
#


async def kernel_run(config_filename):
    kernel_server = None
    url = HASS_URL + "/api/services/pyscript/jupyter_kernel_start"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + HASS_TOKEN,
    }
    #
    # find an available port for our person-in-the-middle heartbeat relay
    #
    with open(config_filename, "r") as fp:
        config = json.load(fp)
    # print(f"got config = {config}")
    client_hb_port = config["hb_port"]
    ip = config["ip"]
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\0" * 8)
    sock.bind((ip, 0))
    kernel_hb_port = sock.getsockname()[1]
    sock.close()
    config["hb_port"] = kernel_hb_port
    # print(f"{sys.argv[0]}: got kernel_hb_port = {kernel_hb_port}, client_hb_port = {client_hb_port}")

    zmq_ctx = zmq.asyncio.Context()
    client_sock = zmq_ctx.socket(zmq.REP)
    client_sock.linger = 1000
    # print(f"binding to client port {zmq_port_url(config['transport'], ip, client_hb_port)}")
    client_sock.bind(zmq_port_url(config["transport"], ip, client_hb_port))

    exit_q = asyncio.Queue(0)

    async def client2kernel(writer):
        """Forward data from kernel to client."""
        while True:
            try:
                data = await client_sock.recv()
                # print(f"{sys.argv[0]}: sending client->kernel {data}")
                writer.write(data)
                await writer.drain()
            except KeyboardInterrupt:
                # print(f"{sys.argv[0]}: Ignoring KeyboardInterrupt")
                pass
            except asyncio.CancelledError:
                raise
            except Exception as err:
                print(
                    f"{sys.argv[0]}: Got exception {err}; {traceback.format_exc(0)}... client returning"
                )
                return

    async def handle_heartbeat(reader, writer):
        """Forward data from kernel to client."""
        nonlocal kernel_server
        # print(f"connected on heartbeat kernel port {ip}:{kernel_hb_port}")
        client_task = asyncio.create_task(client2kernel(writer))
        while True:
            try:
                data = await reader.read(1024)
                if not data:
                    # print(f"{sys.argv[0]}: read EOF from kernel; quitting")
                    break
                # print(f"{sys.argv[0]}: sending kernel data {data}")
                await client_sock.send(data)
            except asyncio.CancelledError:
                raise
            except KeyboardInterrupt:
                # print(f"{sys.argv[0]}: Ignoring KeyboardInterrupt")
                pass
            except Exception as err:
                print(
                    f"{sys.argv[0]}: Got exception {err}; {traceback.format_exc(0)}... exiting"
                )
                break

        try:
            client_task.cancel()
            await client_task
        except asyncio.CancelledError:
            pass
        await exit_q.put(1)
        return

    kernel_server = await asyncio.start_server(handle_heartbeat, ip, kernel_hb_port)

    #
    # call the service
    #
    try:
        _ = requests.request("POST", url, headers=headers, data=json.dumps(config))
    except requests.exceptions.ConnectionError as err:
        print(f"{sys.argv[0]}: unable to connect to {url} ({err})")
        sys.exit(1)
    except requests.exceptions.Timeout as err:
        print(f"{sys.argv[0]}: timeout connecting to {url} ({err})")
        sys.exit(1)
    except Exception as err:
        print(f"{sys.argv[0]}: got error {err} (url={url})")
        sys.exit(1)

    exit_status = await exit_q.get()
    kernel_server.close()
    sys.exit(exit_status)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} configFile.json")
        sys.exit(1)
    asyncio.run(kernel_run(sys.argv[1]))
