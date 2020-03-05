#  Copyright 2019 Will Clark
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
#
#  The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import logging
import socket

import trio


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("PROXYSRV")

SERVER_HOST_PATH = "/tmp/unix_proxy"
CLIENT_HOST_PATH = "/tmp/l2-regtest/unix_socket"
RECV_BUF = 210


async def unlink_socket(sock):
    """Unlink a Unix Socket
    """
    p = trio.Path(sock)
    try:
        await p.unlink()
    except OSError:
        if await p.exists():
            raise


async def proxy(read_stream, write_stream, direction):
    """Proxy traffic from one stream to another
    """
    logger.debug(f"{direction} proxy started")
    while True:
        try:
            # for data in read_stream:
            data = await read_stream.receive_some(RECV_BUF)
            if not data:
                logger.debug("Connection closed by remote")
                break
            await write_stream.send_all(data)
            logger.debug(f"{direction}: {len(data)}B ")
        except:
            raise


async def socket_handler(server_stream):
    """Handles a listening socket. Makes outbound connection to proxy traffic with.
    :arg server_stream: a trio.SocketStream for the listening socket
    """
    client_stream = await trio.open_unix_socket(CLIENT_HOST_PATH)
    try:
        # We run both proxies in a nursery as stream.send_all() can be blocking
        async with trio.open_nursery() as nursery:
            nursery.start_soon(proxy, server_stream, client_stream, "Outbound")
            nursery.start_soon(proxy, client_stream, server_stream, "Inbound")
    except Exception as exc:
        print(f"unix_handler: crashed: {exc}")


async def serve_unix_socket():
    await unlink_socket(SERVER_HOST_PATH)

    # Create the listening socket
    sock = trio.socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    await sock.bind(SERVER_HOST_PATH)
    sock.listen()
    logger.debug(f"Listening on Unix Socket: {SERVER_HOST_PATH}")
    listener = trio.SocketListener(sock)

    # Manage the listening with the handler
    try:
        await trio.serve_listeners(socket_handler, [listener])
    except Exception as exc:
        print(f"serve_unix_socket: crashed: {exc}")
    finally:
        await trio.Path.unlink(SERVER_HOST_PATH)


trio.run(serve_unix_socket)
