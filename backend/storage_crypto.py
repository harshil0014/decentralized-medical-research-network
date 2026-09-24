from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import (
    Cipher,
    algorithms,
    modes,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC_V1 = b"MEDAES01"
MAGIC_V2 = b"MEDAES02"

# Current storage format for NEW encrypted objects.
MAGIC = MAGIC_V2

KEY_VERSION_SIZE = 4

# Version 1 is the initial historical key generation.
# The ACTIVE version is now dataset-specific and stored
# in hospital-local metadata.
INITIAL_KEY_VERSION = 1

# Backward-compatible public constant.
# Do not use this to determine a rotated dataset's active key.
CURRENT_KEY_VERSION = INITIAL_KEY_VERSION

NONCE_SIZE = 12
TAG_SIZE = 16
KEY_SIZE = 32
CHUNK_SIZE = 1024 * 1024

KEY_BLOB_MAGIC = b"MEDKEY01"
KEY_WRAP_NONCE_SIZE = 12


def _master_key() -> bytes:
    raw = os.environ.get(
        "MEDICAL_MASTER_KEY_HEX",
        "",
    ).strip().lower()

    if len(raw) != 64:
        raise RuntimeError(
            "MEDICAL_MASTER_KEY_HEX must provide a 32-byte external wrapping key"
        )

    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise RuntimeError(
            "MEDICAL_MASTER_KEY_HEX must be exactly 64 hexadecimal characters"
        ) from exc

    if len(key) != KEY_SIZE:
        raise RuntimeError(
            "MEDICAL_MASTER_KEY_HEX must provide a 32-byte external wrapping key"
        )

    return key


def _assert_private_file(path: Path) -> None:
    if os.name != "posix":
        return

    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError(
            f"Dataset key file permissions are too broad: {path}"
        )


def _key_wrap_aad(dataset_id: str, key_version: int) -> bytes:
    return (
        KEY_BLOB_MAGIC
        + struct.pack(">I", key_version)
        + hashlib.sha256(dataset_id.encode("utf-8")).digest()
    )


def _wrap_dataset_key(
    dataset_id: str,
    key_version: int,
    key: bytes,
) -> bytes:
    if len(key) != KEY_SIZE:
        raise RuntimeError("Dataset key material is invalid")

    nonce = os.urandom(KEY_WRAP_NONCE_SIZE)
    ciphertext = AESGCM(_master_key()).encrypt(
        nonce,
        key,
        _key_wrap_aad(dataset_id, key_version),
    )
    return KEY_BLOB_MAGIC + nonce + ciphertext


def _atomic_replace_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(
        "."
        + path.name
        + "."
        + str(os.getpid())
        + "."
        + os.urandom(6).hex()
        + ".tmp"
    )

    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
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


def _create_wrapped_key_file(
    path: Path,
    dataset_id: str,
    key_version: int,
    key: bytes,
) -> None:
    payload = _wrap_dataset_key(dataset_id, key_version, key)
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _load_key_material(
    path: Path,
    dataset_id: str,
    key_version: int,
) -> bytes:
    if not path.exists():
        raise FileNotFoundError(
            f"Hospital dataset encryption key version {key_version} not found"
        )

    _assert_private_file(path)
    payload = path.read_bytes()

    if payload.startswith(KEY_BLOB_MAGIC):
        minimum = len(KEY_BLOB_MAGIC) + KEY_WRAP_NONCE_SIZE + 16
        if len(payload) < minimum:
            raise RuntimeError("Wrapped dataset key file is truncated")

        start = len(KEY_BLOB_MAGIC)
        nonce = payload[start:start + KEY_WRAP_NONCE_SIZE]
        ciphertext = payload[start + KEY_WRAP_NONCE_SIZE:]

        try:
            key = AESGCM(_master_key()).decrypt(
                nonce,
                ciphertext,
                _key_wrap_aad(dataset_id, key_version),
            )
        except Exception as exc:
            raise RuntimeError(
                "Dataset key unwrap failed; master key is missing or incorrect"
            ) from exc

        if len(key) != KEY_SIZE:
            raise RuntimeError("Unwrapped dataset key is invalid")

        return key

    # One-time migration path for historical raw 32-byte key files.
    if len(payload) == KEY_SIZE:
        wrapped = _wrap_dataset_key(
            dataset_id,
            key_version,
            payload,
        )
        _atomic_replace_bytes(path, wrapped)
        return payload

    raise RuntimeError(
        f"Stored dataset key version {key_version} is invalid"
    )


def _key_root() -> Path:
    root = Path(
        os.environ.get(
            "MEDICAL_KEY_ROOT",
            str(
                Path.home()
                / ".medical-registry"
                / "dataset_keys"
            ),
        )
    )

    root.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o700,
    )

    try:
        root.chmod(0o700)
    except OSError:
        pass

    return root


def _dataset_key_name(
    dataset_id: str,
) -> str:
    if not dataset_id or not dataset_id.strip():
        raise ValueError(
            "dataset_id is required"
        )

    return hashlib.sha256(
        dataset_id.encode(
            "utf-8"
        )
    ).hexdigest()


def _key_path(
    dataset_id: str,
    key_version: int = INITIAL_KEY_VERSION,
) -> Path:
    if key_version < 1:
        raise ValueError(
            "key_version must be positive"
        )

    name = _dataset_key_name(
        dataset_id
    )

    # Preserve the historical v1 filename exactly.
    if key_version == 1:
        return (
            _key_root()
            / f"{name}.key"
        )

    return (
        _key_root()
        / f"{name}.v{key_version}.key"
    )


def _key_metadata_path(
    dataset_id: str,
) -> Path:
    name = _dataset_key_name(
        dataset_id
    )

    return (
        _key_root()
        / f"{name}.json"
    )


def _utc_now() -> str:
    return (
        datetime.now(
            timezone.utc
        )
        .replace(
            microsecond=0
        )
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


def _atomic_replace_json(
    path: Path,
    payload: dict,
) -> None:
    encoded = (
        json.dumps(
            payload,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")

    temporary = path.with_name(
        "."
        + path.name
        + "."
        + str(os.getpid())
        + "."
        + os.urandom(6).hex()
        + ".tmp"
    )

    fd = os.open(
        temporary,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL,
        0o600,
    )

    try:
        with os.fdopen(
            fd,
            "wb",
        ) as f:
            f.write(
                encoded
            )

            f.flush()
            os.fsync(
                f.fileno()
            )

        os.replace(
            temporary,
            path,
        )

    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

        raise


def _build_key_metadata(
    dataset_id: str,
    created_at: str,
    active_version: int = 1,
) -> dict:
    return {
        "schemaVersion": 2,
        "datasetIdHash": hashlib.sha256(
            dataset_id.encode(
                "utf-8"
            )
        ).hexdigest(),
        "algorithm": "AES-256-GCM",
        "keyProtection": "AES-256-GCM-WRAPPED",

        # Compatibility alias for older code.
        "keyVersion": active_version,

        "activeKeyVersion": active_version,
        "storageFormat": "MEDAES02",
        "createdAt": created_at,
        "updatedAt": created_at,
        "status": "ACTIVE",
        "versions": {
            str(
                active_version
            ): {
                "createdAt": created_at,
                "status": "ACTIVE",
            }
        },
    }


def _write_key_metadata(
    dataset_id: str,
    created_at: str | None = None,
    key_version: int = INITIAL_KEY_VERSION,
    storage_format: str = "MEDAES02",
) -> None:
    if key_version < 1:
        raise ValueError(
            "key_version must be positive"
        )

    if storage_format not in {
        "MEDAES01",
        "MEDAES02",
    }:
        raise ValueError(
            "Unsupported storage format"
        )

    path = _key_metadata_path(
        dataset_id
    )

    if path.exists():
        return

    created = (
        created_at
        or _utc_now()
    )

    payload = _build_key_metadata(
        dataset_id,
        created,
        active_version=key_version,
    )

    # Current writes use MEDAES02.
    # storage_format is retained in the function signature
    # only for backward compatibility with older callers.
    payload[
        "storageFormat"
    ] = "MEDAES02"

    encoded = (
        json.dumps(
            payload,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")

    fd = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL,
        0o600,
    )

    try:
        with os.fdopen(
            fd,
            "wb",
        ) as f:
            f.write(
                encoded
            )

            f.flush()
            os.fsync(
                f.fileno()
            )

    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

        raise


def ensure_dataset_key_metadata(
    dataset_id: str,
) -> None:
    metadata_path = (
        _key_metadata_path(
            dataset_id
        )
    )

    if metadata_path.exists():
        return

    key_path = _key_path(
        dataset_id,
        1,
    )

    if not key_path.exists():
        raise FileNotFoundError(
            "Hospital dataset encryption key not found"
        )

    created = (
        datetime.fromtimestamp(
            key_path.stat().st_mtime,
            timezone.utc,
        )
        .replace(
            microsecond=0
        )
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )

    _write_key_metadata(
        dataset_id,
        created_at=created,
        key_version=1,
        storage_format="MEDAES01",
    )


def load_dataset_key_metadata(
    dataset_id: str,
) -> dict:
    ensure_dataset_key_metadata(
        dataset_id
    )

    path = _key_metadata_path(
        dataset_id
    )

    try:
        metadata = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception as exc:
        raise RuntimeError(
            "Dataset key metadata is invalid"
        ) from exc

    expected_hash = hashlib.sha256(
        dataset_id.encode(
            "utf-8"
        )
    ).hexdigest()

    if (
        metadata.get(
            "datasetIdHash"
        )
        != expected_hash
    ):
        raise RuntimeError(
            "Dataset key metadata identity mismatch"
        )

    if metadata.get(
        "algorithm"
    ) != "AES-256-GCM":
        raise RuntimeError(
            "Unsupported dataset key algorithm"
        )

    # --------------------------------------------------------
    # Automatic migration from the previous metadata schema.
    # --------------------------------------------------------

    schema_version = int(
        metadata.get(
            "schemaVersion",
            1,
        )
    )

    if schema_version == 1:
        legacy_version = int(
            metadata.get(
                "keyVersion",
                1,
            )
        )

        if legacy_version != 1:
            raise RuntimeError(
                "Unsupported legacy dataset key version"
            )

        key_path = _key_path(
            dataset_id,
            1,
        )

        if not key_path.exists():
            raise FileNotFoundError(
                "Historical dataset key v1 is missing"
            )

        created = metadata.get(
            "createdAt"
        )

        if not created:
            created = (
                datetime.fromtimestamp(
                    key_path.stat().st_mtime,
                    timezone.utc,
                )
                .replace(
                    microsecond=0
                )
                .isoformat()
                .replace(
                    "+00:00",
                    "Z",
                )
            )

        metadata = _build_key_metadata(
            dataset_id,
            created,
            active_version=1,
        )

        _atomic_replace_json(
            path,
            metadata,
        )

    elif schema_version != 2:
        raise RuntimeError(
            "Unsupported dataset key metadata schema"
        )

    # --------------------------------------------------------
    # Validate schema 2.
    # --------------------------------------------------------

    active_version = metadata.get(
        "activeKeyVersion"
    )

    if not isinstance(
        active_version,
        int,
    ) or active_version < 1:
        raise RuntimeError(
            "Dataset active key version is invalid"
        )

    if metadata.get(
        "keyVersion"
    ) != active_version:
        raise RuntimeError(
            "Dataset key metadata version mismatch"
        )

    if metadata.get(
        "storageFormat"
    ) != "MEDAES02":
        raise RuntimeError(
            "Unsupported current dataset storage format"
        )

    if metadata.get(
        "status"
    ) != "ACTIVE":
        raise RuntimeError(
            "Dataset key registry is not active"
        )

    versions = metadata.get(
        "versions"
    )

    if not isinstance(
        versions,
        dict,
    ) or not versions:
        raise RuntimeError(
            "Dataset key version registry is invalid"
        )

    for raw_version, record in versions.items():

        try:
            version = int(
                raw_version
            )
        except Exception as exc:
            raise RuntimeError(
                "Dataset key version registry is invalid"
            ) from exc

        if (
            version < 1
            or str(version)
            != raw_version
        ):
            raise RuntimeError(
                "Dataset key version registry is invalid"
            )

        if not isinstance(
            record,
            dict,
        ):
            raise RuntimeError(
                "Dataset key version record is invalid"
            )

        status = record.get(
            "status"
        )

        if status not in {
            "ACTIVE",
            "DECRYPT_ONLY",
        }:
            raise RuntimeError(
                "Dataset key version status is invalid"
            )

        key_path = _key_path(
            dataset_id,
            version,
        )

        if not key_path.exists():
            raise FileNotFoundError(
                "Dataset key version "
                f"{version} is missing"
            )

        key_bytes = _load_key_material(
            key_path,
            dataset_id,
            version,
        )

        if len(key_bytes) != KEY_SIZE:
            raise RuntimeError(
                "Stored dataset key version "
                f"{version} is invalid"
            )

    active_record = versions.get(
        str(
            active_version
        )
    )

    if (
        not active_record
        or active_record.get(
            "status"
        )
        != "ACTIVE"
    ):
        raise RuntimeError(
            "Active dataset key version is not active"
        )

    return metadata


def dataset_key_exists(
    dataset_id: str,
) -> bool:
    name = _dataset_key_name(
        dataset_id
    )

    root = _key_root()

    if (
        root
        / f"{name}.key"
    ).exists():
        return True

    return any(
        root.glob(
            f"{name}.v*.key"
        )
    )


def delete_dataset_key(
    dataset_id: str,
) -> None:
    name = _dataset_key_name(
        dataset_id
    )

    root = _key_root()

    paths = [
        root / f"{name}.key",
        _key_metadata_path(
            dataset_id
        ),
    ]

    paths.extend(
        root.glob(
            f"{name}.v*.key"
        )
    )

    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def get_or_create_dataset_key(
    dataset_id: str,
) -> bytes:
    if dataset_key_exists(
        dataset_id
    ):
        metadata = (
            load_dataset_key_metadata(
                dataset_id
            )
        )

        return load_dataset_key(
            dataset_id,
            int(
                metadata[
                    "activeKeyVersion"
                ]
            ),
        )

    version = 1

    path = _key_path(
        dataset_id,
        version,
    )

    key = os.urandom(
        KEY_SIZE
    )

    _create_wrapped_key_file(
        path,
        dataset_id,
        version,
        key,
    )

    try:
        _write_key_metadata(
            dataset_id,
            key_version=1,
            storage_format="MEDAES02",
        )

    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

        try:
            _key_metadata_path(
                dataset_id
            ).unlink()
        except FileNotFoundError:
            pass

        raise

    return key


def load_dataset_key(
    dataset_id: str,
    key_version: int | None = None,
) -> bytes:
    metadata = (
        load_dataset_key_metadata(
            dataset_id
        )
    )

    if key_version is None:
        key_version = int(
            metadata[
                "activeKeyVersion"
            ]
        )

    if key_version < 1:
        raise ValueError(
            "key_version must be positive"
        )

    versions = metadata[
        "versions"
    ]

    if str(
        key_version
    ) not in versions:
        raise RuntimeError(
            "Dataset key version "
            f"{key_version} is not registered"
        )

    path = _key_path(
        dataset_id,
        key_version,
    )

    if not path.exists():
        raise FileNotFoundError(
            "Hospital dataset encryption key "
            f"version {key_version} not found"
        )

    return _load_key_material(
        path,
        dataset_id,
        key_version,
    )


def rotate_dataset_key(
    dataset_id: str,
) -> dict:
    metadata = (
        load_dataset_key_metadata(
            dataset_id
        )
    )

    previous_version = int(
        metadata[
            "activeKeyVersion"
        ]
    )

    new_version = (
        previous_version
        + 1
    )

    new_path = _key_path(
        dataset_id,
        new_version,
    )

    # Never overwrite a historical or orphan key.
    if new_path.exists():
        raise RuntimeError(
            "Next dataset key version already exists; "
            "refusing to overwrite it"
        )

    new_key = os.urandom(
        KEY_SIZE
    )

    _create_wrapped_key_file(
        new_path,
        dataset_id,
        new_version,
        new_key,
    )

    updated = json.loads(
        json.dumps(
            metadata
        )
    )

    now = _utc_now()

    try:
        updated[
            "versions"
        ][
            str(
                previous_version
            )
        ][
            "status"
        ] = "DECRYPT_ONLY"

        updated[
            "versions"
        ][
            str(
                new_version
            )
        ] = {
            "createdAt": now,
            "status": "ACTIVE",
        }

        updated[
            "activeKeyVersion"
        ] = new_version

        # Compatibility alias.
        updated[
            "keyVersion"
        ] = new_version

        updated[
            "storageFormat"
        ] = "MEDAES02"

        updated[
            "updatedAt"
        ] = now

        _atomic_replace_json(
            _key_metadata_path(
                dataset_id
            ),
            updated,
        )

        # Validate committed registry before reporting success.
        committed = (
            load_dataset_key_metadata(
                dataset_id
            )
        )

    except Exception:
        try:
            new_path.unlink()
        except FileNotFoundError:
            pass

        raise

    return {
        "previousKeyVersion": previous_version,
        "activeKeyVersion": new_version,
        "rotatedAt": committed[
            "updatedAt"
        ],
    }


def rollback_dataset_key_rotation(
    dataset_id: str,
    previous_version: int,
    new_version: int,
) -> dict:
    if (
        previous_version < 1
        or new_version != previous_version + 1
    ):
        raise ValueError(
            "Invalid key rotation rollback versions"
        )

    metadata = load_dataset_key_metadata(
        dataset_id
    )

    active_version = int(
        metadata[
            "activeKeyVersion"
        ]
    )

    if active_version != new_version:
        raise RuntimeError(
            "Cannot rollback key rotation: "
            f"active version is {active_version}, "
            f"expected {new_version}"
        )

    versions = metadata[
        "versions"
    ]

    previous_record = versions.get(
        str(previous_version)
    )

    new_record = versions.get(
        str(new_version)
    )

    if (
        not previous_record
        or not new_record
    ):
        raise RuntimeError(
            "Cannot rollback key rotation: "
            "version registry is incomplete"
        )

    new_path = _key_path(
        dataset_id,
        new_version,
    )

    if not new_path.exists():
        raise RuntimeError(
            "Cannot rollback key rotation: "
            "new key file is missing"
        )

    updated = json.loads(
        json.dumps(
            metadata
        )
    )

    updated[
        "versions"
    ][
        str(previous_version)
    ][
        "status"
    ] = "ACTIVE"

    del updated[
        "versions"
    ][
        str(new_version)
    ]

    updated[
        "activeKeyVersion"
    ] = previous_version

    updated[
        "keyVersion"
    ] = previous_version

    updated[
        "updatedAt"
    ] = _utc_now()

    # First restore metadata so the new generation is no longer
    # referenced. Only then remove the unreferenced key file.
    _atomic_replace_json(
        _key_metadata_path(
            dataset_id
        ),
        updated,
    )

    try:
        new_path.unlink()
    except FileNotFoundError:
        pass

    committed = load_dataset_key_metadata(
        dataset_id
    )

    return {
        "activeKeyVersion": committed[
            "activeKeyVersion"
        ],
        "removedKeyVersion": new_version,
        "rolledBackAt": committed[
            "updatedAt"
        ],
    }


def is_encrypted_dataset(
    data: bytes,
) -> bool:
    return (
        data.startswith(
            MAGIC_V1
        )
        or data.startswith(
            MAGIC_V2
        )
    )


def encrypted_storage_format(
    data: bytes,
) -> str | None:
    if data.startswith(
        MAGIC_V1
    ):
        return "MEDAES01"

    if data.startswith(
        MAGIC_V2
    ):
        return "MEDAES02"

    return None


def encrypted_key_version(
    data: bytes,
) -> int | None:
    if data.startswith(
        MAGIC_V1
    ):
        # MEDAES01 existed before an explicit version field.
        return 1

    if not data.startswith(
        MAGIC_V2
    ):
        return None

    minimum = (
        len(MAGIC_V2)
        + KEY_VERSION_SIZE
    )

    if len(data) < minimum:
        raise ValueError(
            "MEDAES02 header is truncated"
        )

    start = len(
        MAGIC_V2
    )

    end = (
        start
        + KEY_VERSION_SIZE
    )

    return struct.unpack(
        ">I",
        data[start:end],
    )[0]


def encrypt_file(
    dataset_id: str,
    source: Path,
    destination: Path,
) -> str:
    # Creates v1 only for a brand-new dataset.
    get_or_create_dataset_key(
        dataset_id
    )

    metadata = (
        load_dataset_key_metadata(
            dataset_id
        )
    )

    key_version = int(
        metadata[
            "activeKeyVersion"
        ]
    )

    key = load_dataset_key(
        dataset_id,
        key_version,
    )

    nonce = os.urandom(
        NONCE_SIZE
    )

    version_bytes = (
        struct.pack(
            ">I",
            key_version,
        )
    )

    authenticated_header = (
        MAGIC_V2
        + version_bytes
    )

    encryptor = Cipher(
        algorithms.AES(key),
        modes.GCM(nonce),
    ).encryptor()

    encryptor.authenticate_additional_data(
        authenticated_header
        + dataset_id.encode(
            "utf-8"
        )
    )

    sha256 = hashlib.sha256()

    with source.open("rb") as src:
        with destination.open("wb") as dst:

            header = (
                authenticated_header
                + nonce
            )

            dst.write(
                header
            )

            sha256.update(
                header
            )

            while True:
                chunk = src.read(
                    CHUNK_SIZE
                )

                if not chunk:
                    break

                encrypted = (
                    encryptor.update(
                        chunk
                    )
                )

                if encrypted:
                    dst.write(
                        encrypted
                    )

                    sha256.update(
                        encrypted
                    )

            final = (
                encryptor.finalize()
            )

            if final:
                dst.write(
                    final
                )

                sha256.update(
                    final
                )

            tag = encryptor.tag

            dst.write(
                tag
            )

            sha256.update(
                tag
            )

    return sha256.hexdigest()


def decrypt_bytes(
    dataset_id: str,
    encrypted: bytes,
) -> bytes:

    # --------------------------------------------------------
    # MEDAES01 — historical format always uses v1.
    # --------------------------------------------------------

    if encrypted.startswith(
        MAGIC_V1
    ):
        minimum = (
            len(MAGIC_V1)
            + NONCE_SIZE
            + TAG_SIZE
        )

        if len(encrypted) < minimum:
            raise ValueError(
                "MEDAES01 dataset is truncated"
            )

        offset = len(
            MAGIC_V1
        )

        nonce = encrypted[
            offset:
            offset + NONCE_SIZE
        ]

        ciphertext = encrypted[
            offset + NONCE_SIZE:
            -TAG_SIZE
        ]

        tag = encrypted[
            -TAG_SIZE:
        ]

        key = load_dataset_key(
            dataset_id,
            1,
        )

        decryptor = Cipher(
            algorithms.AES(key),
            modes.GCM(
                nonce,
                tag,
            ),
        ).decryptor()

        decryptor.authenticate_additional_data(
            dataset_id.encode(
                "utf-8"
            )
        )

        return (
            decryptor.update(
                ciphertext
            )
            + decryptor.finalize()
        )

    # --------------------------------------------------------
    # MEDAES02 — header selects exact historical key version.
    # --------------------------------------------------------

    if encrypted.startswith(
        MAGIC_V2
    ):
        minimum = (
            len(MAGIC_V2)
            + KEY_VERSION_SIZE
            + NONCE_SIZE
            + TAG_SIZE
        )

        if len(encrypted) < minimum:
            raise ValueError(
                "MEDAES02 dataset is truncated"
            )

        magic_end = len(
            MAGIC_V2
        )

        version_end = (
            magic_end
            + KEY_VERSION_SIZE
        )

        version_bytes = encrypted[
            magic_end:
            version_end
        ]

        key_version = struct.unpack(
            ">I",
            version_bytes,
        )[0]

        if key_version < 1:
            raise ValueError(
                "MEDAES02 key version is invalid"
            )

        nonce_end = (
            version_end
            + NONCE_SIZE
        )

        nonce = encrypted[
            version_end:
            nonce_end
        ]

        ciphertext = encrypted[
            nonce_end:
            -TAG_SIZE
        ]

        tag = encrypted[
            -TAG_SIZE:
        ]

        key = load_dataset_key(
            dataset_id,
            key_version,
        )

        decryptor = Cipher(
            algorithms.AES(key),
            modes.GCM(
                nonce,
                tag,
            ),
        ).decryptor()

        authenticated_header = (
            MAGIC_V2
            + version_bytes
        )

        decryptor.authenticate_additional_data(
            authenticated_header
            + dataset_id.encode(
                "utf-8"
            )
        )

        return (
            decryptor.update(
                ciphertext
            )
            + decryptor.finalize()
        )

    raise ValueError(
        "Dataset is not a supported MEDAES object"
    )


def sha256_bytes(
    data: bytes,
) -> str:
    return hashlib.sha256(
        data
    ).hexdigest()
