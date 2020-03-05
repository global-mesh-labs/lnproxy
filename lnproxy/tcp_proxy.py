import logging

import trio


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("PROXYSRV")

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 19999
CLIENT_HOST = "127.0.0.1"
CLIENT_PORT = 19733
RECV_BUF = 210


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
    client_stream = await trio.open_tcp_stream(CLIENT_HOST, CLIENT_PORT)
    try:
        # We run both proxies in a nursery as stream.send_all() can be blocking
        async with trio.open_nursery() as nursery:
            nursery.start_soon(proxy, server_stream, client_stream, "Outbound")
            nursery.start_soon(proxy, client_stream, server_stream, "Inbound")
    except Exception as exc:
        print(f"socket_handler: crashed: {exc}")


async def serve_tcp_server():
    await trio.serve_tcp(socket_handler, SERVER_PORT)


trio.run(serve_tcp_server)
