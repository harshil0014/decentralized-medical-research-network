from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import (
    Cipher,
    algorithms,
    modes,
)


MAGIC = b"MEDAES01"
NONCE_SIZE = 12
TAG_SIZE = 16
KEY_SIZE = 32
CHUNK_SIZE = 1024 * 1024


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


def _key_path(
    dataset_id: str,
) -> Path:
    if not dataset_id or not dataset_id.strip():
        raise ValueError(
            "dataset_id is required"
        )

    name = hashlib.sha256(
        dataset_id.encode("utf-8")
    ).hexdigest()

    return _key_root() / f"{name}.key"


def _key_metadata_path(
    dataset_id: str,
) -> Path:
    return _key_path(
        dataset_id
    ).with_suffix(".json")


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


def _write_key_metadata(
    dataset_id: str,
    created_at: str | None = None,
) -> None:
    path = _key_metadata_path(
        dataset_id
    )

    payload = {
        "schemaVersion": 1,
        "datasetIdHash": hashlib.sha256(
            dataset_id.encode(
                "utf-8"
            )
        ).hexdigest(),
        "algorithm": "AES-256-GCM",
        "keyVersion": 1,
        "storageFormat": "MEDAES01",
        "createdAt": (
            created_at
            or _utc_now()
        ),
        "status": "ACTIVE",
    }

    encoded = (
        json.dumps(
            payload,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")

    try:
        fd = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        return

    with os.fdopen(
        fd,
        "wb",
    ) as f:
        f.write(encoded)


def ensure_dataset_key_metadata(
    dataset_id: str,
) -> None:
    key_path = _key_path(
        dataset_id
    )

    metadata_path = (
        _key_metadata_path(
            dataset_id
        )
    )

    if metadata_path.exists():
        return

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

    if metadata.get(
        "keyVersion"
    ) != 1:
        raise RuntimeError(
            "Unsupported dataset key version"
        )

    if metadata.get(
        "status"
    ) != "ACTIVE":
        raise RuntimeError(
            "Dataset key is not active"
        )

    return metadata


def dataset_key_exists(
    dataset_id: str,
) -> bool:
    return _key_path(
        dataset_id
    ).exists()


def delete_dataset_key(
    dataset_id: str,
) -> None:
    for path in (
        _key_path(
            dataset_id
        ),
        _key_metadata_path(
            dataset_id
        ),
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def get_or_create_dataset_key(
    dataset_id: str,
) -> bytes:
    path = _key_path(dataset_id)

    if path.exists():
        key = path.read_bytes()

        if len(key) != KEY_SIZE:
            raise RuntimeError(
                "Stored dataset key is invalid"
            )

        ensure_dataset_key_metadata(
            dataset_id
        )

        return key

    key = os.urandom(KEY_SIZE)

    fd = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL,
        0o600,
    )

    try:
        with os.fdopen(fd, "wb") as f:
            f.write(key)
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise

    try:
        _write_key_metadata(
            dataset_id
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
) -> bytes:
    path = _key_path(dataset_id)

    if not path.exists():
        raise FileNotFoundError(
            "Hospital dataset encryption key not found"
        )

    key = path.read_bytes()

    if len(key) != KEY_SIZE:
        raise RuntimeError(
            "Stored dataset key is invalid"
        )

    load_dataset_key_metadata(
        dataset_id
    )

    return key


def encrypt_file(
    dataset_id: str,
    source: Path,
    destination: Path,
) -> str:
    key = get_or_create_dataset_key(
        dataset_id
    )

    nonce = os.urandom(
        NONCE_SIZE
    )

    encryptor = Cipher(
        algorithms.AES(key),
        modes.GCM(nonce),
    ).encryptor()

    # Bind the encrypted object to its Fabric dataset ID.
    encryptor.authenticate_additional_data(
        dataset_id.encode("utf-8")
    )

    sha256 = hashlib.sha256()

    with source.open("rb") as src:
        with destination.open("wb") as dst:

            header = MAGIC + nonce

            dst.write(header)
            sha256.update(header)

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

            final = encryptor.finalize()

            if final:
                dst.write(final)
                sha256.update(final)

            tag = encryptor.tag

            dst.write(tag)
            sha256.update(tag)

    return sha256.hexdigest()


def decrypt_bytes(
    dataset_id: str,
    encrypted: bytes,
) -> bytes:
    minimum = (
        len(MAGIC)
        + NONCE_SIZE
        + TAG_SIZE
    )

    if len(encrypted) < minimum:
        raise ValueError(
            "Encrypted dataset is truncated"
        )

    if not encrypted.startswith(
        MAGIC
    ):
        raise ValueError(
            "Dataset is not in MEDAES01 format"
        )

    offset = len(MAGIC)

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
        dataset_id
    )

    decryptor = Cipher(
        algorithms.AES(key),
        modes.GCM(
            nonce,
            tag,
        ),
    ).decryptor()

    decryptor.authenticate_additional_data(
        dataset_id.encode("utf-8")
    )

    return (
        decryptor.update(
            ciphertext
        )
        + decryptor.finalize()
    )


def sha256_bytes(
    data: bytes,
) -> str:
    return hashlib.sha256(
        data
    ).hexdigest()
