"""Integration check for the four-validator, three-peer demo."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen

from backend import ipfs_storage


RPC_URLS = [f"http://127.0.0.1:{port}" for port in range(8545, 8549)]
BESU = [f"medical-besu-{index}" for index in range(1, 5)]
IPFS = ["medical-ipfs", "medical-ipfs-2", "medical-ipfs-3"]


def rpc(url, method, params=None):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                          "params": params or []}).encode()
    request = Request(url, payload, {"Content-Type": "application/json"})
    with urlopen(request, timeout=15) as response:
        result = json.load(response)
    if "error" in result:
        raise RuntimeError(result["error"])
    return result["result"]


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def wait_rpc(url):
    for _ in range(30):
        try:
            return rpc(url, "eth_blockNumber")
        except Exception:
            time.sleep(2)
    raise RuntimeError(f"RPC did not become ready: {url}")


def main():
    deployment = json.loads(Path("ethereum/deployment.json").read_text())
    address = deployment["contractAddress"]
    assert all(docker("inspect", "-f", "{{.State.Status}}", name) == "running"
               for name in BESU + IPFS)

    enodes = [rpc(url, "admin_nodeInfo")["enode"] for url in RPC_URLS]
    assert len(set(enodes)) == 4, "Besu RPC endpoints must be distinct nodes"
    validators = [rpc(url, "qbft_getValidatorsByBlockNumber", ["latest"])
                  for url in RPC_URLS]
    assert all(len(set(items)) == 4 for items in validators)
    assert all(set(items) == set(validators[0]) for items in validators)
    assert all(int(rpc(url, "eth_chainId"), 16) == deployment["chainId"]
               for url in RPC_URLS)
    codes = [rpc(url, "eth_getCode", [address, "latest"]) for url in RPC_URLS]
    assert len(set(codes)) == 1 and codes[0] != "0x", "Contract state differs across nodes"
    peer_ids = [docker("exec", name, "ipfs", "id", "-f=<id>") for name in IPFS]
    assert len(set(peer_ids)) == 3, "IPFS peers must have distinct identities"
    if "--status-only" in sys.argv:
        print("DECENTRALIZED DEMO: HEALTHY (4 validators, 3 IPFS peers, shared state)")
        return
    os.environ["MEDICAL_ETHEREUM_RPC_URLS"] = ",".join(RPC_URLS)
    from backend.ethereum_ledger import health as backend_ethereum_health
    assert backend_ethereum_health()["contractAddress"].lower() == address.lower()
    os.environ["MEDICAL_IPFS_CONTAINERS"] = ",".join(IPFS)
    sample = os.urandom(128)
    with tempfile.NamedTemporaryFile(delete=False) as handle:
        handle.write(sample)
        path = Path(handle.name)
    cid = None
    try:
        cid = ipfs_storage.add_file(path)
        for name in IPFS:
            assert cid in docker("exec", name, "ipfs", "pin", "ls", cid)
        docker("stop", IPFS[0])
        assert ipfs_storage.cat(cid) == sample, "Replicated read failed after primary outage"
    finally:
        docker("start", IPFS[0])
        for _ in range(30):
            if docker("inspect", "-f", "{{.State.Health.Status}}", IPFS[0]) == "healthy":
                break
            time.sleep(2)
        else:
            raise RuntimeError("Primary IPFS peer did not become healthy after restart")
        path.unlink(missing_ok=True)
        if cid:
            ipfs_storage.unpin(cid)

    docker("stop", BESU[0])
    try:
        # Four QBFT validators retain quorum with one validator unavailable.
        assert all(wait_rpc(url) is not None for url in RPC_URLS[1:])
        assert all(rpc(url, "eth_getCode", [address, "latest"]) == codes[0]
                   for url in RPC_URLS[1:])
        assert backend_ethereum_health()["connected"] is True
    finally:
        docker("start", BESU[0])
        for _ in range(45):
            try:
                first_height = int(rpc(RPC_URLS[0], "eth_blockNumber"), 16)
                second_height = int(rpc(RPC_URLS[1], "eth_blockNumber"), 16)
                peers = int(rpc(RPC_URLS[0], "net_peerCount"), 16)
                if first_height >= second_height and peers >= 1:
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            raise RuntimeError("Primary Besu node did not resync after restart")
    print("DECENTRALIZED TOPOLOGY: PASS (4 validators, 3 IPFS peers, outages)")


if __name__ == "__main__":
    main()
