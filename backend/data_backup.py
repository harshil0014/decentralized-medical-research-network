from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path


_DATASET_ID = re.compile(r"^ds-[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _backup_root() -> Path:
    raw = os.environ.get("MEDICAL_DATA_BACKUP_DIR", "").strip()
    if not raw:
        raise RuntimeError(
            "MEDICAL_DATA_BACKUP_DIR must point to a separate encrypted-object backup destination"
        )

    root = Path(raw).resolve()
    key_root = Path(
        os.environ.get(
            "MEDICAL_KEY_ROOT",
            str(Path.home() / ".medical-registry" / "dataset_keys"),
        )
    ).resolve()
    auth_root = Path(
        os.environ.get(
            "MEDICAL_REGISTRY_AUTH_DIR",
            "/root/.medical-registry",
        )
    ).resolve()

    for protected_root, label in (
        (key_root, "dataset key directory"),
        (auth_root, "authentication/private-locator directory"),
    ):
        try:
            root.relative_to(protected_root)
        except ValueError:
            continue
        raise RuntimeError(
            f"Encrypted-object backup must not be inside the {label}"
        )

    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def _paths(dataset_id: str) -> tuple[Path, Path]:
    if not _DATASET_ID.fullmatch(dataset_id):
        raise ValueError("Invalid dataset ID for backup")
    root = _backup_root()
    return (
        root / f"{dataset_id}.medobj",
        root / f"{dataset_id}.json",
    )


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(
        "." + path.name + "." + uuid.uuid4().hex + ".tmp"
    )
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_encrypted_dataset(
    dataset_id: str,
    source: Path,
    cid: str,
    sha256: str,
) -> dict:
    sha256 = sha256.strip().lower()
    if not _SHA256.fullmatch(sha256):
        raise ValueError("Invalid encrypted-object SHA-256")

    object_path, metadata_path = _paths(dataset_id)
    temporary = object_path.with_name(
        "." + object_path.name + "." + uuid.uuid4().hex + ".tmp"
    )

    with source.open("rb") as src, open(temporary, "xb") as dst:
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        shutil.copyfileobj(src, dst, length=1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())

    actual = _sha256_file(temporary)
    if actual != sha256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Encrypted-object backup SHA-256 mismatch")

    os.replace(temporary, object_path)
    try:
        object_path.chmod(0o600)
    except OSError:
        pass

    metadata = {
        "schemaVersion": 1,
        "datasetId": dataset_id,
        "cid": str(cid),
        "sha256": sha256,
        "size": object_path.stat().st_size,
    }
    _atomic_write(
        metadata_path,
        (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )
    return metadata


def delete_dataset_backup(dataset_id: str) -> None:
    object_path, metadata_path = _paths(dataset_id)
    object_path.unlink(missing_ok=True)
    metadata_path.unlink(missing_ok=True)


def _read_metadata(dataset_id: str) -> dict:
    object_path, metadata_path = _paths(dataset_id)
    if not object_path.exists() or not metadata_path.exists():
        raise RuntimeError(
            f"Encrypted-object backup is missing for {dataset_id}"
        )

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Encrypted-object backup metadata is corrupt for {dataset_id}"
        ) from exc

    if (
        metadata.get("schemaVersion") != 1
        or metadata.get("datasetId") != dataset_id
        or not _SHA256.fullmatch(str(metadata.get("sha256") or ""))
    ):
        raise RuntimeError(
            f"Encrypted-object backup metadata is invalid for {dataset_id}"
        )

    actual = _sha256_file(object_path)
    if actual != metadata["sha256"]:
        raise RuntimeError(
            f"Encrypted-object backup integrity check failed for {dataset_id}"
        )

    if int(metadata.get("size", -1)) != object_path.stat().st_size:
        raise RuntimeError(
            f"Encrypted-object backup size check failed for {dataset_id}"
        )

    return metadata


def collect_backup_manifest() -> dict[str, dict]:
    root = _backup_root()
    result: dict[str, dict] = {}
    for path in sorted(root.glob("ds-*.json")):
        dataset_id = path.stem
        if not _DATASET_ID.fullmatch(dataset_id):
            continue
        result[dataset_id] = _read_metadata(dataset_id)
    return result


def _ipfs_has(cid: str) -> bool:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "medical-ipfs",
            "ipfs",
            "cat",
            cid,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=180,
    )
    return result.returncode == 0


def restore_missing_ipfs_objects(
    locators: dict,
    *,
    expected_manifest: dict | None = None,
) -> dict:
    restored = 0
    already_present = 0

    for dataset_id, locator in sorted(locators.items()):
        cid = str((locator or {}).get("cid") or "").strip()
        sha256 = str((locator or {}).get("sha256") or "").strip().lower()
        if not cid or not _SHA256.fullmatch(sha256):
            raise RuntimeError(
                f"Private locator is invalid for {dataset_id}"
            )

        metadata = _read_metadata(dataset_id)
        if metadata["cid"] != cid or metadata["sha256"] != sha256:
            raise RuntimeError(
                f"Encrypted-object backup does not match locator for {dataset_id}"
            )

        if expected_manifest is not None:
            expected = expected_manifest.get(dataset_id)
            if expected != metadata:
                raise RuntimeError(
                    f"Recovery bundle backup manifest mismatch for {dataset_id}"
                )

        if _ipfs_has(cid):
            already_present += 1
            continue

        object_path, _ = _paths(dataset_id)
        container_path = f"/tmp/medical-recovery-{uuid.uuid4().hex}.medaes"
        try:
            copied = subprocess.run(
                [
                    "docker",
                    "cp",
                    str(object_path),
                    f"medical-ipfs:{container_path}",
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if copied.returncode != 0:
                raise RuntimeError(
                    copied.stderr.strip()
                    or f"Failed to copy backup object for {dataset_id}"
                )

            added = subprocess.run(
                [
                    "docker",
                    "exec",
                    "medical-ipfs",
                    "ipfs",
                    "add",
                    "-Q",
                    container_path,
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if added.returncode != 0:
                raise RuntimeError(
                    added.stderr.strip()
                    or f"Failed to restore IPFS object for {dataset_id}"
                )

            restored_cid = added.stdout.strip()
            if restored_cid != cid:
                raise RuntimeError(
                    f"Restored IPFS CID mismatch for {dataset_id}"
                )
            restored += 1
        finally:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "medical-ipfs",
                    "rm",
                    "-f",
                    container_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    return {
        "restoredIpfsObjects": restored,
        "existingIpfsObjects": already_present,
        "verifiedObjectBackups": len(locators),
    }
