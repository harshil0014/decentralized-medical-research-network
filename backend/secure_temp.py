from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


_ALLOWED_MEMORY_FS = {"tmpfs", "ramfs"}


def _linux_mount_type(path: Path) -> str | None:
    if not sys.platform.startswith("linux"):
        return None

    target = path.resolve()
    best_mount = None
    best_type = None

    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        try:
            left, right = line.split(" - ", 1)
            left_fields = left.split()
            right_fields = right.split()
            mount_point = Path(left_fields[4].replace("\\040", " ")).resolve()
            fs_type = right_fields[0]
        except Exception:
            continue

        try:
            target.relative_to(mount_point)
        except ValueError:
            continue

        if best_mount is None or len(str(mount_point)) > len(str(best_mount)):
            best_mount = mount_point
            best_type = fs_type

    return best_type


def secure_plaintext_temp_root() -> Path:
    configured = os.environ.get("MEDICAL_PLAINTEXT_TMPDIR", "").strip()

    if configured:
        root = Path(configured)
    elif sys.platform.startswith("linux") and Path("/dev/shm").is_dir():
        root = Path("/dev/shm") / "medical-registry-plaintext"
    else:
        raise RuntimeError(
            "No RAM-backed plaintext staging area is configured. "
            "Set MEDICAL_PLAINTEXT_TMPDIR to a tmpfs/ramfs path."
        )

    root.mkdir(parents=True, exist_ok=True, mode=0o700)

    try:
        root.chmod(0o700)
    except OSError:
        pass

    fs_type = _linux_mount_type(root)

    if sys.platform.startswith("linux"):
        if fs_type not in _ALLOWED_MEMORY_FS:
            raise RuntimeError(
                f"Plaintext staging path must be tmpfs/ramfs, got {fs_type or 'unknown'}: {root}"
            )
    elif os.environ.get("MEDICAL_ALLOW_NONLINUX_RAM_STAGING", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise RuntimeError(
            "Plaintext staging is fail-closed on non-Linux platforms. "
            "Run the secure API in WSL/Linux or explicitly provide an audited RAM-backed implementation."
        )

    return root


def create_secure_plaintext_temp(*, suffix: str = "") -> str:
    root = secure_plaintext_temp_root()

    fd, path = tempfile.mkstemp(
        prefix="medical-plaintext-",
        suffix=suffix,
        dir=root,
    )

    os.close(fd)

    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

    return path
