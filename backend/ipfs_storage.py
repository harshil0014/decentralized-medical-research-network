from __future__ import annotations

import os
from pathlib import Path
import subprocess
import uuid


def ipfs_containers() -> tuple[str, ...]:
    raw = os.environ.get("MEDICAL_IPFS_CONTAINERS", "medical-ipfs")
    names = tuple(
        item.strip()
        for item in raw.split(",")
        if item.strip()
    )
    if not names:
        raise RuntimeError("At least one IPFS container must be configured")
    if len(set(names)) != len(names):
        raise RuntimeError("Duplicate IPFS container names are not allowed")
    return names


def primary_ipfs_container() -> str:
    return ipfs_containers()[0]


def _exec(container: str, args: list[str], *, timeout: int = 180, binary: bool = False):
    return subprocess.run(
        ["docker", "exec", container, "ipfs", *args],
        capture_output=True,
        text=not binary,
        timeout=timeout,
    )


def replicate_cid(cid: str) -> None:
    if not cid:
        raise ValueError("IPFS CID is required")
    failures: list[str] = []
    for container in ipfs_containers()[1:]:
        result = _exec(container, ["pin", "add", cid], timeout=240)
        if result.returncode != 0:
            failures.append(
                f"{container}: {(result.stderr or result.stdout or '').strip()}"
            )
    if failures:
        raise RuntimeError(
            "IPFS replication failed: " + "; ".join(failures)
        )


def add_file(path: str | Path) -> str:
    source = Path(path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"IPFS source file not found: {source}")

    primary = primary_ipfs_container()
    container_path = f"/tmp/medical-ipfs-{uuid.uuid4().hex}{source.suffix}"
    try:
        copied = subprocess.run(
            ["docker", "cp", str(source), f"{primary}:{container_path}"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if copied.returncode != 0:
            raise RuntimeError(
                copied.stderr.strip()
                or f"Failed to copy artifact into {primary}"
            )

        added = _exec(primary, ["add", "-Q", container_path], timeout=240)
        if added.returncode != 0:
            raise RuntimeError(
                (added.stderr or added.stdout or "").strip()
                or "IPFS add failed"
            )

        cid = added.stdout.strip()
        if not cid:
            raise RuntimeError("IPFS returned an empty CID")

        replicate_cid(cid)
        return cid
    finally:
        subprocess.run(
            ["docker", "exec", primary, "rm", "-f", container_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def cat(cid: str, *, timeout: int = 180) -> bytes:
    if not cid or not cid.strip():
        raise ValueError("IPFS CID is required")

    errors: list[str] = []
    for container in ipfs_containers():
        result = _exec(container, ["cat", cid], timeout=timeout, binary=True)
        if result.returncode == 0:
            return result.stdout
        errors.append(
            f"{container}: {result.stderr.decode('utf-8', errors='replace').strip()}"
        )

    raise RuntimeError(
        "IPFS retrieval failed on every configured peer: " + "; ".join(errors)
    )


def has(cid: str) -> bool:
    try:
        cat(cid, timeout=60)
        return True
    except Exception:
        return False


def unpin(cid: str) -> None:
    if not cid:
        return
    for container in ipfs_containers():
        _exec(container, ["pin", "rm", cid], timeout=60)


def health() -> dict:
    peers: list[dict] = []
    for container in ipfs_containers():
        inspected = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", container],
            capture_output=True,
            text=True,
        )
        swarm = _exec(container, ["swarm", "peers"], timeout=30)
        peer_count = 0
        if swarm.returncode == 0:
            peer_count = len(
                [line for line in swarm.stdout.splitlines() if line.strip()]
            )
        peers.append(
            {
                "container": container,
                "status": inspected.stdout.strip() if inspected.returncode == 0 else "unavailable",
                "swarmPeers": peer_count,
            }
        )
    return {
        "nodes": peers,
        "nodeCount": len(peers),
        "replicationFactor": len(peers),
    }
