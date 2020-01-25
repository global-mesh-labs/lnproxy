#!/usr/bin/env python3
import logging
import pathlib
import time
import uuid

import lightning
import trio
from goTenna.constants import GID_MAX
from secp256k1 import PublicKey

import lnproxy.config as config
import lnproxy.network as network
import lnproxy.util as util
from lnproxy.mesh_connection import connection_daemon
from lnproxy.messages import EncryptedMessage
from lnproxy.pk_from_hsm import get_privkey
from lnproxy.proxy import serve_outbound

gotenna_plugin = lightning.Plugin()
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("plugin")


gotenna_plugin.add_option(
    name="gid",
    default=None,
    description="A GID for connected goTenna device to use",
    opt_type="int",
)


@gotenna_plugin.method("node-info")
def node_info(plugin=None):
    node_pubkey = plugin.rpc.getinfo()["id"]
    node_gid = plugin.get_option("gid")
    return {"node_pubkey": node_pubkey, "node_gid": node_gid}


@gotenna_plugin.method("add-node")
def add_node(gid, pubkey, plugin=None):
    """Add a mesh-connected node to the routing table.
    arg: gid: integer within valid goTenna GID range
    arg: pubkey: a node's lightning pubkey
    """
    # Check that GID and pubkey are valid
    assert 0 <= gid <= GID_MAX
    # TODO: check pubkey is valid
    # try:
    #     _pubkey = PublicKey(pubkey=pubkey.encode("utf-8"), raw=True)
    # except Exception:
    #     logger.exception("Error with pubkey")

    # Add to router
    _node = network.Node(int(gid), str(pubkey))
    network.router.add(_node)
    return f"{_node} added to gotenna plugin router:\n{network.router}"


@gotenna_plugin.method("proxy-connect")
def proxy_connect(gid, plugin=None):
    """Connect to a remote node via goTenna mesh proxy.
    """
    logging.debug(f"proxy-connect to gid {gid} via goTenna mesh connection")
    # Generate a random fd to listen on for this outbound connection.
    listen_addr = f"/tmp/0{uuid.uuid1().hex}"
    # Setup the listening server for C-Lightning to connect through, started in the
    # main shared nursery.
    trio.from_thread.run(config.nursery.start, serve_outbound, f"{listen_addr}", gid)
    # Confirm the socket is created and listening.
    while not pathlib.Path(listen_addr).is_socket():
        time.sleep(0.1)
    # Instruct C-Lightning RPC to connect to remote via the socket after it has been
    # established.
    pubkey = network.router.lookup_pubkey(gid)
    return plugin.rpc.connect(str(pubkey), f"{listen_addr}")


@gotenna_plugin.method("message")
def message(
    gid, message_string, plugin=None,
):
    """sendpay via the mesh connection using key-send (non-interactive)
    args: (goTenna) gid, msatoshi, [label]
    """
    # Get the destination pubkey
    dest_pubkey = network.router.lookup_pubkey(gid)
    _message = EncryptedMessage(
        gid=gid, plain_text=message_string, dest_pubkey=dest_pubkey,
    )
    try:
        _message.encrypt()
    except LookupError:
        logger.exception("Can't find pubkey or nonce needed for encryption in router")
    except TypeError:
        logger.exception("Invalid public key type for encryption")
    else:
        logger.debug(f"Encrypted message:\n{_message.encrypted_msg.hex()}")

    # Create a pseudo-random description to satisfy C-Lightning's accounting system
    description = f"{uuid.uuid1().hex} encrypted message to {_message.dest_pubkey}"

    # Store message for later.
    # We will add the encrypted message onto the outbound htlc_add_update message
    config.key_sends[_message.payment_hash.digest()] = _message
    logger.debug(
        f"Stored message at config.key_sends[{_message.payment_hash.digest()}]"
    )

    # Get next peer in route
    peer = config.rpc.listfunds()["channels"][0]["peer_id"]
    logger.debug(f"Got next peer {peer}")

    # We will use 100 satoshis as base payment amount
    msatoshi = 100_000
    # We add 10 satoshis to amount (10 hops max x 1 satoshi fee each)
    # We add 60 to cltv (10 hops max, CLTV of 6 each)
    amt_msat = msatoshi + 10
    cltv = 9 + 60

    route = config.rpc.getroute(peer, msatoshi=amt_msat, riskfactor=10, cltv=cltv)[
        "route"
    ]
    logger.info(f"Got route to {peer}, executing sendpay command.")

    return config.rpc.sendpay(
        route, _message.payment_hash.hexdigest(), description, amt_msat
    )


@gotenna_plugin.init()
# Unused parameters used by lightning.plugin() internally
def init(options, configuration, plugin):
    logger.info("Starting goTenna plugin")
    # Store the RPC in config to be accessible by all modules.
    config.rpc = plugin.rpc
    # Get the local lightning node info.
    config.node_info = plugin.rpc.getinfo()
    logger.info(config.node_info)
    # ======= WARNING =======
    # Store our node private key for message decryption
    config.node_secret_key = str(get_privkey(config.node_info["lightning-dir"]))
    logger.info(f"Node private key: {config.node_secret_key}")
    # ===== END WARNING =====
    util.write_pubkey_to_file()
    # Hack to get other node pubkeys
    # TODO: fix this with new router implementation
    # util.read_pubkeys_from_files()
    # Suppress all gossip messages from C-Lightning node.
    plugin.rpc.dev_suppress_gossip()
    logger.info("goTenna plugin initialized")


async def main():
    """Main function that is run when the plugin is loaded (and run) by C-Lightning.
    Function decorated with @plugin.init() will be run when `plugin.run()` is called
    which must be at startup.
    """
    # This nursery will run our main tasks for us:
    try:
        async with trio.open_nursery() as config.nursery:
            # We run the plugin itself in a synchronous thread so trio.run() maintains
            # overall control of the app.
            config.nursery.start_soon(trio.to_thread.run_sync, gotenna_plugin.run)
            # Start the goTenna connection daemon.
            # config.nursery.start_soon(connection_daemon)
    except:
        logger.exception("Exception in main loop:")
    finally:
        logger.info("goTenna plugin finished.")


trio.run(main)
