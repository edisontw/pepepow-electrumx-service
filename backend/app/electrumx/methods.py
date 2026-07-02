from typing import Any

from .client import ElectrumXClient

SERVER_VERSION = "server.version"
SERVER_FEATURES = "server.features"
HEADERS_SUBSCRIBE = "blockchain.headers.subscribe"
SCRIPTHASH_GET_BALANCE = "blockchain.scripthash.get_balance"
SCRIPTHASH_GET_HISTORY = "blockchain.scripthash.get_history"
SCRIPTHASH_GET_MEMPOOL = "blockchain.scripthash.get_mempool"
SCRIPTHASH_LIST_UNSPENT = "blockchain.scripthash.listunspent"
TRANSACTION_GET = "blockchain.transaction.get"
TRANSACTION_BROADCAST = "blockchain.transaction.broadcast"


async def server_version(client: ElectrumXClient) -> Any:
    return await client.request(SERVER_VERSION, ["pepew-light", "1.4"])


async def server_features(client: ElectrumXClient) -> Any:
    return await client.request(SERVER_FEATURES)


async def headers_subscribe(client: ElectrumXClient) -> Any:
    return await client.request(HEADERS_SUBSCRIBE)


async def scripthash_get_balance(client: ElectrumXClient, scripthash: str) -> Any:
    return await client.request(SCRIPTHASH_GET_BALANCE, [scripthash])


async def scripthash_get_history(client: ElectrumXClient, scripthash: str) -> Any:
    return await client.request(SCRIPTHASH_GET_HISTORY, [scripthash])


async def scripthash_get_mempool(client: ElectrumXClient, scripthash: str) -> Any:
    return await client.request(SCRIPTHASH_GET_MEMPOOL, [scripthash])


async def scripthash_list_unspent(client: ElectrumXClient, scripthash: str) -> Any:
    return await client.request(SCRIPTHASH_LIST_UNSPENT, [scripthash])


async def transaction_get(client: ElectrumXClient, txid: str, verbose: bool = False) -> Any:
    # Some ElectrumX forks accept blockchain.transaction.get(txid) for raw hex
    # but reject blockchain.transaction.get(txid, false). Use the optional
    # verbose flag only when verbose JSON is explicitly requested.
    params = [txid, True] if verbose else [txid]
    return await client.request(TRANSACTION_GET, params)


async def transaction_broadcast(client: ElectrumXClient, raw_tx: str) -> Any:
    return await client.request(TRANSACTION_BROADCAST, [raw_tx])
