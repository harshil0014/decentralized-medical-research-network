from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


MAGIC = b"MEDREC01"
NONCE_SIZE = 12
_KEY_FILE = re.compile(
    r"^(?:[0-9a-f]{64}\.key|[0-9a-f]{64}\.v[1-9][0-9]*\.key|[0-9a-f]{64}\.json)$"
)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _key_root() -> Path:
    return Path(
        os.environ.get(
            "MEDICAL_KEY_ROOT",
            str(Path.home() / ".medical-registry" / "dataset_keys"),
        )
    )


def _locator_path() -> Path:
    auth_root = Path(
        os.environ.get(
            "MEDICAL_REGISTRY_AUTH_DIR",
            "/root/.medical-registry",
        )
    )
    return Path(
        os.environ.get(
            "MEDICAL_ETHEREUM_PRIVATE_LOCATORS",
            str(auth_root / "ethereum_private_locators.json"),
        )
    )


def recovery_backup_path() -> Path:
    raw = os.environ.get("MEDICAL_RECOVERY_BACKUP_PATH", "").strip()
    if not raw:
        raise RuntimeError(
            "MEDICAL_RECOVERY_BACKUP_PATH must point to a separate backup destination"
        )

    path = Path(raw)
    key_root = _key_root().resolve()

    try:
        path.resolve().relative_to(key_root)
    except ValueError:
        pass
    else:
        raise RuntimeError(
            "Recovery backup must not be stored inside the dataset key directory"
        )

    return path


def _master_key() -> bytes:
    raw = os.environ.get("MEDICAL_MASTER_KEY_HEX", "").strip()
    if len(raw) != 64:
        raise RuntimeError(
            "MEDICAL_MASTER_KEY_HEX must provide a 32-byte external secret"
        )
    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise RuntimeError(
            "MEDICAL_MASTER_KEY_HEX must be hexadecimal"
        ) from exc
    if len(key) != 32:
        raise RuntimeError(
            "MEDICAL_MASTER_KEY_HEX must provide a 32-byte external secret"
        )
    return key


def _recovery_key() -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=hashlib.sha256(b"medical-registry-recovery-v1").digest(),
        info=b"medical-registry-recovery-bundle",
    ).derive(_master_key())


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_locators() -> dict:
    path = _locator_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Private locator store is corrupt") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Private locator store is invalid")
    return value


def _collect_key_files() -> dict[str, str]:
    root = _key_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)

    files: dict[str, str] = {}
    for path in sorted(root.iterdir()):
        if not path.is_file() or not _KEY_FILE.fullmatch(path.name):
            continue
        files[path.name] = base64.b64encode(path.read_bytes()).decode("ascii")
    return files


def _ledger_fingerprint() -> dict:
    from backend.ethereum_ledger import health

    state = health()
    return {
        "chainId": int(state["chainId"]),
        "contractAddress": str(state["contractAddress"]).lower(),
    }


def _build_payload() -> dict:
    key_files = _collect_key_files()
    locators = _read_locators()

    manifest = {
        "keyFiles": {
            name: hashlib.sha256(base64.b64decode(value)).hexdigest()
            for name, value in key_files.items()
        },
        "privateLocatorsSha256": hashlib.sha256(
            _canonical(locators)
        ).hexdigest(),
    }

    return {
        "schemaVersion": 1,
        "createdAt": _utc_now(),
        "ledger": _ledger_fingerprint(),
        "keyFiles": key_files,
        "privateLocators": locators,
        "manifest": manifest,
    }


def export_recovery_bundle() -> bytes:
    payload = _build_payload()
    plaintext = _canonical(payload)
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = AESGCM(_recovery_key()).encrypt(
        nonce,
        plaintext,
        MAGIC,
    )
    return MAGIC + nonce + ciphertext


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass

    temporary = path.with_name(
        "." + path.name + "." + os.urandom(6).hex() + ".tmp"
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


def snapshot_recovery_bundle() -> dict:
    path = recovery_backup_path()
    bundle = export_recovery_bundle()
    _atomic_write(path, bundle)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(bundle).hexdigest(),
        "size": len(bundle),
    }


def _decode_bundle(bundle: bytes) -> dict:
    if not bundle.startswith(MAGIC):
        raise ValueError("Recovery bundle magic is invalid")

    if len(bundle) < len(MAGIC) + NONCE_SIZE + 16:
        raise ValueError("Recovery bundle is truncated")

    offset = len(MAGIC)
    nonce = bundle[offset:offset + NONCE_SIZE]
    ciphertext = bundle[offset + NONCE_SIZE:]

    try:
        plaintext = AESGCM(_recovery_key()).decrypt(
            nonce,
            ciphertext,
            MAGIC,
        )
    except Exception as exc:
        raise ValueError(
            "Recovery bundle authentication failed"
        ) from exc

    try:
        payload = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        raise ValueError("Recovery bundle payload is invalid") from exc

    if payload.get("schemaVersion") != 1:
        raise ValueError("Unsupported recovery bundle schema")

    current = _ledger_fingerprint()
    recorded = payload.get("ledger") or {}
    if (
        int(recorded.get("chainId", -1)) != current["chainId"]
        or str(recorded.get("contractAddress", "")).lower()
        != current["contractAddress"]
    ):
        raise ValueError(
            "Recovery bundle belongs to a different chain or contract"
        )

    key_files = payload.get("keyFiles")
    locators = payload.get("privateLocators")
    manifest = payload.get("manifest")

    if not isinstance(key_files, dict) or not isinstance(locators, dict):
        raise ValueError("Recovery bundle content is invalid")
    if not isinstance(manifest, dict):
        raise ValueError("Recovery bundle manifest is missing")

    manifest_keys = manifest.get("keyFiles")
    if not isinstance(manifest_keys, dict):
        raise ValueError("Recovery key manifest is invalid")

    for name, encoded in key_files.items():
        if not _KEY_FILE.fullmatch(name):
            raise ValueError("Recovery bundle contains an invalid key filename")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValueError("Recovery key file encoding is invalid") from exc
        if hashlib.sha256(raw).hexdigest() != manifest_keys.get(name):
            raise ValueError("Recovery key file integrity check failed")

    if hashlib.sha256(_canonical(locators)).hexdigest() != manifest.get(
        "privateLocatorsSha256"
    ):
        raise ValueError("Recovery private locator integrity check failed")

    return payload


def restore_recovery_bundle(
    bundle: bytes,
    *,
    replace_existing: bool = False,
) -> dict:
    payload = _decode_bundle(bundle)

    key_root = _key_root()
    locator_path = _locator_path()
    key_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    key_files = payload["keyFiles"]
    locators = payload["privateLocators"]

    targets = {
        key_root / name: base64.b64decode(encoded)
        for name, encoded in key_files.items()
    }

    if not replace_existing:
        conflicts = [
            str(path)
            for path in targets
            if path.exists()
        ]
        if locator_path.exists():
            conflicts.append(str(locator_path))
        if conflicts:
            raise RuntimeError(
                "Recovery restore would overwrite existing state; "
                "set replace_existing=true only for an intentional restore"
            )

    managed_existing = {
        path
        for path in key_root.iterdir()
        if path.is_file() and _KEY_FILE.fullmatch(path.name)
    }

    original_files: dict[Path, bytes | None] = {
        path: path.read_bytes() if path.exists() else None
        for path in (managed_existing | set(targets))
    }
    original_locator = (
        locator_path.read_bytes()
        if locator_path.exists()
        else None
    )

    try:
        for path, raw in targets.items():
            _atomic_write(path, raw)

        if replace_existing:
            for stale in sorted(managed_existing - set(targets)):
                stale.unlink()

        _atomic_write(
            locator_path,
            (
                json.dumps(
                    locators,
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8"),
        )

        from backend.storage_crypto import load_dataset_key_metadata
        from backend.ethereum_ledger import query

        verified = 0
        for dataset_id in sorted(locators):
            load_dataset_key_metadata(dataset_id)
            query("ReadDatasetPrivate", [dataset_id], "org1")
            verified += 1

    except Exception:
        current_managed = {
            path
            for path in key_root.iterdir()
            if path.is_file() and _KEY_FILE.fullmatch(path.name)
        }

        for path in current_managed - set(original_files):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

        for path, original in original_files.items():
            if original is None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            else:
                _atomic_write(path, original)

        if original_locator is None:
            try:
                locator_path.unlink()
            except FileNotFoundError:
                pass
        else:
            _atomic_write(locator_path, original_locator)

        raise

    return {
        "restoredKeyFiles": len(targets),
        "restoredPrivateLocators": len(locators),
        "verifiedDatasets": verified,
    }
