from __future__ import annotations

import hashlib
import json
import os
import struct
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import (
    Cipher,
    algorithms,
    modes,
)


MAGIC_V1 = b"MEDAES01"
MAGIC_V2 = b"MEDAES02"

# Current storage format for NEW encrypted objects.
MAGIC = MAGIC_V2

KEY_VERSION_SIZE = 4
CURRENT_KEY_VERSION = 1

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
    key_version: int = CURRENT_KEY_VERSION,
    storage_format: str = "MEDAES02",
) -> None:
    path = _key_metadata_path(
        dataset_id
    )

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

    payload = {
        "schemaVersion": 1,
        "datasetIdHash": hashlib.sha256(
            dataset_id.encode(
                "utf-8"
            )
        ).hexdigest(),
        "algorithm": "AES-256-GCM",
        "keyVersion": key_version,
        "storageFormat": storage_format,
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

    # A key that predates metadata/version support belongs
    # to the original MEDAES01 generation.
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

    if metadata.get(
        "keyVersion"
    ) != CURRENT_KEY_VERSION:
        raise RuntimeError(
            "Unsupported dataset key version"
        )

    if metadata.get(
        "storageFormat"
    ) not in {
        "MEDAES01",
        "MEDAES02",
    }:
        raise RuntimeError(
            "Unsupported dataset storage format"
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
            dataset_id,
            key_version=CURRENT_KEY_VERSION,
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
    key = get_or_create_dataset_key(
        dataset_id
    )

    metadata = (
        load_dataset_key_metadata(
            dataset_id
        )
    )

    key_version = int(
        metadata["keyVersion"]
    )

    if (
        key_version
        != CURRENT_KEY_VERSION
    ):
        raise RuntimeError(
            "Unsupported encryption key version"
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

    # Bind ciphertext to:
    # - dataset identity
    # - storage format
    # - key version
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
    # MEDAES01 — legacy format
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
            dataset_id
        )

        decryptor = Cipher(
            algorithms.AES(key),
            modes.GCM(
                nonce,
                tag,
            ),
        ).decryptor()

        # Original MEDAES01 AAD.
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
    # MEDAES02 — explicit key version
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

        if (
            key_version
            != CURRENT_KEY_VERSION
        ):
            raise RuntimeError(
                "Dataset requires unsupported "
                f"key version {key_version}"
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
            dataset_id
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
