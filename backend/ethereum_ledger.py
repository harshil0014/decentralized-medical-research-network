from __future__ import annotations

import json
import hashlib
import fcntl
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from web3 import Web3


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_PATH = Path(
    os.environ.get(
        "MEDICAL_ETHEREUM_CONFIG",
        str(PROJECT_ROOT / "ethereum" / "deployment.json"),
    )
)
AUTH_ROOT = Path(
    os.environ.get(
        "MEDICAL_REGISTRY_AUTH_DIR",
        "/root/.medical-registry",
    )
)
PRIVATE_LOCATOR_PATH = Path(
    os.environ.get(
        "MEDICAL_ETHEREUM_PRIVATE_LOCATORS",
        str(AUTH_ROOT / "ethereum_private_locators.json"),
    )
)
HE_LOCATOR_PATH = Path(
    os.environ.get("MEDICAL_ETHEREUM_HE_LOCATORS", str(AUTH_ROOT / "ethereum_he_locators.json"))
)


class _FailoverHTTPProvider(Web3.HTTPProvider):
    """Retry transport failures against configured RPC replicas."""

    def __init__(self, urls: list[str], expected_chain_id: int | None):
        super().__init__(urls[0], request_kwargs={"timeout": 10})
        self._replicas = [
            Web3.HTTPProvider(url, request_kwargs={"timeout": 10})
            for url in urls
        ]
        self._active = 0
        self._expected_chain_id = expected_chain_id
        self._validated: set[int] = set()

    def make_request(self, method, params):
        last_error = None
        for offset in range(len(self._replicas)):
            index = (self._active + offset) % len(self._replicas)
            try:
                if self._expected_chain_id is not None and index not in self._validated:
                    chain = self._replicas[index].make_request("eth_chainId", [])
                    if int(chain.get("result", "0x0"), 16) != self._expected_chain_id:
                        raise RuntimeError("Ethereum RPC chain ID differs from deployment")
                    self._validated.add(index)
                response = self._replicas[index].make_request(method, params)
            except Exception as exc:
                last_error = exc
                continue
            self._active = index
            return response
        raise RuntimeError("All Ethereum RPC endpoints are unavailable") from last_error


def _http_error(action: str, exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=f"Ethereum {action} is unavailable or transaction state is uncertain; check ledger state before retrying",
    )


@lru_cache(maxsize=1)
def _deployment() -> dict[str, Any]:
    try:
        data = json.loads(DEPLOYMENT_PATH.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Ethereum deployment file is missing: {DEPLOYMENT_PATH}. "
            "Run scripts/setup_ethereum_local.sh first."
        ) from exc
    required = {
        "rpcUrl",
        "contractAddress",
        "hospitalAddress",
        "abiPath",
    }
    missing = sorted(required - data.keys())
    if missing:
        raise RuntimeError(
            "Ethereum deployment is incomplete: "
            + ", ".join(missing)
        )
    return data


@lru_cache(maxsize=1)
def _web3() -> Web3:
    data = _deployment()
    configured = os.environ.get("MEDICAL_ETHEREUM_RPC_URLS", "")
    urls = [url.strip() for url in configured.split(",") if url.strip()]
    if not urls:
        urls = [data["rpcUrl"]]
    expected_chain_id = int(data["chainId"]) if data.get("chainId") is not None else None
    w3 = Web3(_FailoverHTTPProvider(urls, expected_chain_id))
    if not w3.is_connected():
        raise RuntimeError("Cannot connect to any configured Ethereum RPC")
    if data.get("chainId") is not None and w3.eth.chain_id != int(data["chainId"]):
        raise RuntimeError("Ethereum RPC chain ID differs from deployment")
    return w3


@lru_cache(maxsize=1)
def _contract():
    data = _deployment()
    abi_path = Path(data["abiPath"])
    if not abi_path.is_absolute():
        abi_path = PROJECT_ROOT / abi_path
    abi = json.loads(abi_path.read_text())
    return _web3().eth.contract(
        address=Web3.to_checksum_address(data["contractAddress"]),
        abi=abi,
    )


def _account(org: str) -> str:
    data = _deployment()

    if org == "org1":
        return Web3.to_checksum_address(data["hospitalAddress"])

    if org == "org2":
        # Read-only compatibility caller; no researcher transaction is ever
        # signed by the backend.
        return Web3.to_checksum_address(data["hospitalAddress"])

    if org.startswith("researcher-address:"):
        raw_address = org.split(":", 1)[1]
        if not Web3.is_address(raw_address):
            raise ValueError(f"Invalid researcher address role: {org}")
        return Web3.to_checksum_address(raw_address)

    raise ValueError(f"Unknown role: {org}")


def account_address(org: str) -> str:
    return _account(org)


def _hospital_private_key() -> str | None:
    key_file = os.environ.get("MEDICAL_HOSPITAL_PRIVATE_KEY_FILE", "").strip()
    if not key_file:
        return None

    path = Path(key_file)
    if not path.exists():
        raise RuntimeError(f"Hospital Ethereum key file is missing: {path}")
    if os.name == "posix" and (path.stat().st_mode & 0o077):
        raise RuntimeError("Hospital Ethereum key file permissions are too broad")

    value = path.read_text(encoding="utf-8").strip()
    if not value.startswith("0x"):
        value = "0x" + value
    if len(value) != 66:
        raise RuntimeError("Hospital Ethereum private key is invalid")

    derived = _web3().eth.account.from_key(value).address
    expected = _account("org1")
    if derived.lower() != expected.lower():
        raise RuntimeError(
            "Hospital Ethereum private key does not match deployment authority"
        )
    return value


def _org_name(address: str) -> str:
    data = _deployment()
    value = (address or "").lower()

    if value == data["hospitalAddress"].lower():
        return "Hospital"

    return address


def _iso(timestamp: int) -> str:
    if not timestamp:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(
        int(timestamp),
        tz=timezone.utc,
    ).isoformat().replace("+00:00", "Z")


def _load_locators() -> dict[str, dict[str, str]]:
    if not PRIVATE_LOCATOR_PATH.exists():
        return {}
    try:
        return json.loads(PRIVATE_LOCATOR_PATH.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Private locator store is corrupt: {PRIVATE_LOCATOR_PATH}"
        ) from exc


def _save_locators(data: dict[str, dict[str, str]]) -> None:
    PRIVATE_LOCATOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = PRIVATE_LOCATOR_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.chmod(temp, 0o600)
    os.replace(temp, PRIVATE_LOCATOR_PATH)
    os.chmod(PRIVATE_LOCATOR_PATH, 0o600)


def _locator_commitment(cid: str, sha256: str):
    return Web3.keccak(text=f"{cid}:{sha256.lower()}")


def _he_locator_commitment(cid: str) -> str:
    return "sha256:" + hashlib.sha256(cid.encode("utf-8")).hexdigest()


def _load_he_locators() -> dict:
    if not HE_LOCATOR_PATH.exists():
        return {}
    data = json.loads(HE_LOCATOR_PATH.read_text())
    if not isinstance(data, dict):
        raise RuntimeError("Private HE locator store is invalid")
    return data


def _store_he_locator(job_id: str, name: str, cid: str) -> None:
    HE_LOCATOR_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = HE_LOCATOR_PATH.with_suffix(".lock")
    with open(lock_path, "a+") as lock_file:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            data = _load_he_locators()
            entry = data.setdefault(job_id, {})
            old = entry.get(name)
            if old and old != cid:
                raise RuntimeError("HE artifact locator already bound to another CID")
            entry[name] = cid
            temp = HE_LOCATOR_PATH.with_suffix(".tmp")
            temp.write_text(json.dumps(data, sort_keys=True))
            os.chmod(temp, 0o600)
            os.replace(temp, HE_LOCATOR_PATH)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _dataset(row) -> dict[str, Any]:
    return {
        "datasetId": row[0],
        "ownerOrg": _org_name(row[1]),
        "ownerAddress": row[1],
        "dataType": row[2],
        "metadataSummary": row[3],
        "consentState": row[4],
        "storageState": row[5],
        "locatorCommitment": Web3.to_hex(row[6]),
        "version": int(row[7]),
        "createdAt": _iso(row[8]),
        "updatedAt": _iso(row[9]),
    }


def _request(row) -> dict[str, Any]:
    return {
        "requestId": row[0],
        "datasetId": row[1],
        "requesterOrg": _org_name(row[2]),
        "requesterAddress": row[2],
        "purpose": row[3],
        "status": row[4],
        "requestedAt": _iso(row[5]),
        "decidedAt": _iso(row[6]),
        "decidedBy": "" if int(row[7], 16) == 0 else _org_name(row[7]),
        "decidedByAddress": "" if int(row[7], 16) == 0 else row[7],
    }


def _rotation(row) -> dict[str, Any]:
    return {
        "datasetId": row[0],
        "previousKeyVersion": int(row[1]),
        "newKeyVersion": int(row[2]),
        "ownerOrg": _org_name(row[3]),
        "ownerAddress": row[3],
        "status": row[4],
        "rotatedAt": _iso(row[5]),
    }


def _he_job(row, org: str = "org2", *, historical: bool = False) -> dict[str, Any]:
    private = org == "org1" or (
        org.startswith("researcher-address:")
        and org.split(":", 1)[1].lower() == str(row[12]).lower()
    )
    locators = _load_he_locators().get(row[0], {}) if private else {}
    ciphertext_cid = locators.get("ciphertextCid", "")
    result_cid = locators.get("resultCid", "")
    if ciphertext_cid and _he_locator_commitment(ciphertext_cid) != row[7]:
        raise RuntimeError("HE ciphertext locator commitment mismatch")
    if result_cid and _he_locator_commitment(result_cid) != row[9]:
        if historical:
            result_cid = ""
        else:
            raise RuntimeError("HE result locator commitment mismatch")
    return {
        "jobId": row[0],
        "datasetId": row[1],
        "requestId": row[2],
        "secondaryDatasetId": row[3],
        "secondaryRequestId": row[4],
        "metric": row[5],
        "cohortSize": int(row[6]),
        "ciphertextCid": ciphertext_cid,
        "ciphertextLocatorCommitment": row[7],
        "ciphertextManifestSha256": row[8],
        "resultCid": result_cid,
        "resultLocatorCommitment": row[9],
        "resultSha256": row[10],
        "ownerOrg": _org_name(row[11]),
        "ownerAddress": row[11],
        "researcherOrg": _org_name(row[12]),
        "researcherAddress": row[12],
        "status": row[13],
        "createdAt": _iso(row[14]),
        "computedAt": _iso(row[15]),
        "decryptedAt": _iso(row[16]),
    }


def _send(method: str, args: list[Any], org: str):
    try:
        w3 = _web3()
        fn = getattr(_contract().functions, method)(*args)
        sender = _account(org)
        estimated_gas = fn.estimate_gas({"from": sender})
        gas_limit = max(
            estimated_gas * 2,
            estimated_gas + 100_000,
        )

        private_key = _hospital_private_key() if org == "org1" else None
        if private_key:
            nonce = w3.eth.get_transaction_count(sender, "pending")
            transaction = fn.build_transaction(
                {
                    "from": sender,
                    "nonce": nonce,
                    "gas": gas_limit,
                    "chainId": w3.eth.chain_id,
                    "gasPrice": w3.eth.gas_price,
                }
            )
            signed = w3.eth.account.sign_transaction(
                transaction,
                private_key=private_key,
            )
            tx_hash = w3.eth.send_raw_transaction(
                signed.raw_transaction
            )
        else:
            tx_hash = fn.transact({
                "from": sender,
                "gas": gas_limit,
            })

        receipt = w3.eth.wait_for_transaction_receipt(
            tx_hash,
            timeout=60,
        )
        if receipt.status != 1:
            raise RuntimeError(
                f"transaction reverted: {Web3.to_hex(tx_hash)}"
            )
        return receipt
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(method, exc) from exc

def _call(method: str, args: list[Any], org: str):
    try:
        fn = getattr(_contract().functions, method)(*args)
        return fn.call({"from": _account(org)})
    except Exception as exc:
        raise _http_error(method, exc) from exc


def health() -> dict[str, Any]:
    w3 = _web3()
    data = _deployment()
    return {
        "connected": w3.is_connected(),
        "network": data.get("network", "Ethereum"),
        "chainId": w3.eth.chain_id,
        "blockNumber": w3.eth.block_number,
        "contractAddress": data["contractAddress"],
        "hospitalAddress": data["hospitalAddress"],
        "researcherSigning": "external-eip191",
    }


def invoke_private_org1(
    function: str,
    args: list[str],
    transient: dict,
) -> None:
    if function != "StoreDatasetLocatorPrivate":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported private ledger operation: {function}",
        )
    if not args:
        raise HTTPException(status_code=400, detail="dataset ID required")
    locator = transient.get("dataset_locator")
    if not isinstance(locator, dict):
        raise HTTPException(
            status_code=400,
            detail="dataset_locator transient object required",
        )
    cid = str(locator.get("cid", "")).strip()
    sha256 = str(locator.get("sha256", "")).strip().lower()
    if not cid or len(sha256) != 64:
        raise HTTPException(
            status_code=400,
            detail="valid CID and SHA-256 are required",
        )
    data = _load_locators()
    data[args[0]] = {"cid": cid, "sha256": sha256}
    _save_locators(data)


def invoke(function: str, args: list[str], org: str) -> None:
    if function == "RegisterDataset":
        _send("registerDataset", args[:4], org)
        return

    if function == "FinalizeDatasetRegistration":
        dataset_id = args[0]
        locator = _load_locators().get(dataset_id)
        if not locator:
            raise HTTPException(
                status_code=409,
                detail="Hospital private dataset locator is missing",
            )
        commitment = _locator_commitment(
            locator["cid"],
            locator["sha256"],
        )
        _send("finalizeDataset", [dataset_id, commitment], org)
        return

    if function == "CancelPendingDatasetRegistration":
        dataset_id = args[0]
        _send("cancelPendingDatasetRegistration", [dataset_id], org)
        data = _load_locators()
        if dataset_id in data:
            del data[dataset_id]
            _save_locators(data)
        return

    if function == "UpdateConsent":
        _send("updateConsent", args[:2], org)
        return

    if function == "RequestAccessSigned":
        _send(
            "requestAccessBySig",
            [args[0], args[1], args[2], Web3.to_checksum_address(args[3]),
             bytes.fromhex(args[4].removeprefix("0x"))],
            "org1",
        )
        return

    if function == "DecideAccess":
        _send("decideAccess", args[:2], org)
        return

    if function == "RecordKeyRotation":
        _send(
            "recordKeyRotation",
            [args[0], int(args[1]), int(args[2])],
            org,
        )
        return

    if function == "ReplaceDatasetCiphertext":
        _send(
            "replaceDatasetCiphertext",
            [args[0], Web3.to_bytes(hexstr=args[1]), Web3.to_bytes(hexstr=args[2]),
             int(args[3]), int(args[4])],
            org,
        )
        return

    if function == "RecordDatasetMigration":
        _send("recordDatasetMigration", [args[0], int(args[1]), Web3.to_checksum_address(args[2])], org)
        return

    if function == "RegisterHEJob":
        _send(
            "registerHEJob",
            [
                args[0],
                args[1],
                args[2],
                args[3],
                args[4],
                args[5],
                int(args[6]),
                _he_locator_commitment(args[7]),
                args[8],
            ],
            org,
        )
        _store_he_locator(args[0], "ciphertextCid", args[7])
        return

    if function == "RecordHEComputationSigned":
        _send(
            "recordHEComputationBySig",
            [
                args[0],
                _he_locator_commitment(args[1]),
                args[2],
                bytes.fromhex(args[3].removeprefix("0x")),
            ],
            "org1",
        )
        _store_he_locator(args[0], "resultCid", args[1])
        return

    if function == "RecordHEDecryption":
        _send("recordHEDecryption", args[:1], org)
        return

    raise HTTPException(
        status_code=400,
        detail=f"Unsupported Ethereum invoke: {function}",
    )


def query(function: str, args: list[str], org: str = "org2") -> str:
    if function == "DatasetExists":
        return "true" if _call("datasetExists", args[:1], org) else "false"

    if function == "DatasetPrivateLocatorExists":
        return "true" if args[0] in _load_locators() else "false"

    if function == "DiscoverDataset" or function == "ReadDataset":
        return json.dumps(_dataset(_call("getDataset", args[:1], org)))

    if function == "ReadDatasetPrivate":
        if org != "org1":
            raise HTTPException(
                status_code=403,
                detail="Hospital role required for private dataset locator",
            )
        public = _dataset(_call("getDataset", args[:1], org))
        locator = _load_locators().get(args[0])
        if not locator:
            raise HTTPException(
                status_code=404,
                detail=f"Private locator for {args[0]} is missing",
            )
        expected = Web3.to_hex(
            _locator_commitment(locator["cid"], locator["sha256"])
        ).lower()
        actual = public["locatorCommitment"].lower()
        if public["storageState"] == "PRIVATE_READY" and actual != expected:
            raise HTTPException(
                status_code=409,
                detail="Ethereum locator commitment verification failed",
            )
        public.update(locator)
        return json.dumps(public)

    if function == "ReadDatasetLocatorPrivate":
        locator = _load_locators().get(args[0])
        if not locator:
            raise HTTPException(status_code=404, detail="private locator missing")
        return json.dumps(locator)

    if function == "GetAllDatasets":
        offset = int(args[0]) if args else 0
        limit = int(args[1]) if len(args) > 1 else 50
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(status_code=400, detail="Invalid pagination")
        return json.dumps([
            _dataset(item)
            for item in _call("getDatasetPage", [offset, limit], org)
            if item[10]
        ])

    if function == "DatasetCount":
        return str(_call("datasetCount", [], org))

    if function == "MigrationSource":
        return Web3.to_hex(_call("migrationSource", args[:1], org))

    if function == "GetDatasetHistory":
        history = []
        offset = int(args[1]) if len(args) > 1 else 0
        limit = int(args[2]) if len(args) > 2 else 50
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(status_code=400, detail="Invalid pagination")
        for index, row in enumerate(
            _call("getDatasetHistoryPage", [args[0], offset, limit], org)
        ):
            value = _dataset(row)
            history.append({
                "txId": f"ethereum-history-{offset + index + 1}",
                "timestamp": value["updatedAt"],
                "isDelete": False,
                "value": value,
            })
        return json.dumps(history)

    if function == "ResearcherRequestDigest":
        value = _call(
            "researcherRequestDigest",
            args[:3],
            org,
        )
        return Web3.to_hex(value)

    if function == "HEComputeDigest":
        value = _call(
            "heComputeDigest",
            args[:1],
            org,
        )
        return Web3.to_hex(value)

    if function == "ReadAccessRequest":
        return json.dumps(
            _request(_call("getAccessRequest", args[:1], org))
        )

    if function == "CanAccess":
        return "true" if _call("canAccess", args[:1], org) else "false"

    if function == "GetAccessHistory":
        history = []
        offset = int(args[1]) if len(args) > 1 else 0
        limit = int(args[2]) if len(args) > 2 else 50
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(status_code=400, detail="Invalid pagination")
        for index, row in enumerate(
            _call("getAccessHistoryPage", [args[0], offset, limit], org)
        ):
            value = _request(row)
            history.append({
                "txId": f"ethereum-history-{offset + index + 1}",
                "timestamp": value["decidedAt"] or value["requestedAt"],
                "isDelete": False,
                "value": value,
            })
        return json.dumps(history)

    if function == "KeyRotationExists":
        value = _call(
            "keyRotationExists",
            [args[0], int(args[1])],
            org,
        )
        return "true" if value else "false"

    if function == "ReadKeyRotation":
        return json.dumps(
            _rotation(
                _call(
                    "getKeyRotation",
                    [args[0], int(args[1])],
                    org,
                )
            )
        )

    if function == "ReadHEJob":
        return json.dumps(_he_job(_call("getHEJob", args[:1], org), org))

    if function == "GetHEJobHistory":
        history = []
        offset = int(args[1]) if len(args) > 1 else 0
        limit = int(args[2]) if len(args) > 2 else 50
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(status_code=400, detail="Invalid pagination")
        for index, row in enumerate(
            _call("getHEJobHistoryPage", [args[0], offset, limit], org)
        ):
            value = _he_job(row, org, historical=True)
            timestamp = (
                value["decryptedAt"]
                or value["computedAt"]
                or value["createdAt"]
            )
            history.append({
                "txId": f"ethereum-history-{offset + index + 1}",
                "timestamp": timestamp,
                "isDelete": False,
                "value": value,
            })
        return json.dumps(history)

    raise HTTPException(
        status_code=400,
        detail=f"Unsupported Ethereum query: {function}",
    )
