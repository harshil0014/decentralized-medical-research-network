from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hmac
import json
import os
import re
import stat

from web3 import Web3

from fastapi import Depends, Header, HTTPException


AUTH_ROOT = Path(
    os.environ.get(
        "MEDICAL_REGISTRY_AUTH_DIR",
        "/root/.medical-registry",
    )
)

HOSPITAL_TOKEN_PATH = AUTH_ROOT / "hospital_api.token"
RESEARCHER_REGISTRY_PATH = Path(
    os.environ.get(
        "MEDICAL_RESEARCHER_REGISTRY",
        str(AUTH_ROOT / "researchers.json"),
    )
)

_RESEARCHER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _assert_private_path(path: Path, *, directory: bool = False) -> None:
    if os.name != "posix":
        return

    mode = stat.S_IMODE(path.stat().st_mode)
    forbidden = 0o077
    if mode & forbidden:
        kind = "directory" if directory else "file"
        raise RuntimeError(
            f"Authentication {kind} permissions are too broad: {path}"
        )


@dataclass(frozen=True)
class AuthIdentity:
    role: str
    researcher_id: str | None = None
    wallet_address: str | None = None


@dataclass(frozen=True)
class _ResearcherCredential:
    researcher_id: str
    wallet_address: str
    token: str
    enabled: bool


def _load_token(path: Path) -> str:
    try:
        _assert_private_path(path)
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"API token file is missing: {path}"
        ) from exc

    if (
        len(token) != 64
        or any(ch not in "0123456789abcdef" for ch in token)
    ):
        raise RuntimeError(
            f"API token file is malformed: {path}"
        )

    return token


def _resolve_token_file(raw: str) -> Path:
    if not raw or Path(raw).name != raw:
        raise RuntimeError(
            "Researcher tokenFile must be a filename inside the auth directory"
        )

    path = (AUTH_ROOT / raw).resolve()
    root = AUTH_ROOT.resolve()

    if path.parent != root:
        raise RuntimeError(
            "Researcher tokenFile must remain inside the auth directory"
        )

    return path


def _load_researchers() -> tuple[_ResearcherCredential, ...]:
    try:
        _assert_private_path(AUTH_ROOT, directory=True)
        _assert_private_path(RESEARCHER_REGISTRY_PATH)
        payload = json.loads(
            RESEARCHER_REGISTRY_PATH.read_text(encoding="utf-8")
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Researcher registry is missing: {RESEARCHER_REGISTRY_PATH}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Researcher registry is invalid JSON") from exc

    if payload.get("schemaVersion") != 2:
        raise RuntimeError(
            "Researcher registry schemaVersion must be 2 with externally owned walletAddress values"
        )

    rows = payload.get("researchers")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Researcher registry must contain at least one researcher")

    credentials: list[_ResearcherCredential] = []
    seen_ids: set[str] = set()
    seen_wallets: set[str] = set()
    seen_tokens: set[str] = set()

    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Researcher registry entry must be an object")

        researcher_id = str(row.get("id") or "").strip()
        wallet_address = str(row.get("walletAddress") or "").strip()
        token_file = str(row.get("tokenFile") or "").strip()
        enabled = row.get("enabled", True)
        if not isinstance(enabled, bool):
            raise RuntimeError("Researcher enabled must be boolean")

        if not _RESEARCHER_ID.fullmatch(researcher_id):
            raise RuntimeError("Researcher id is invalid")

        if not Web3.is_address(wallet_address):
            raise RuntimeError("Researcher walletAddress must be a valid Ethereum address")
        wallet_address = Web3.to_checksum_address(wallet_address)

        token_path = _resolve_token_file(token_file)
        if not token_path.exists():
            # Deleted credentials are revoked immediately.
            continue
        token = _load_token(token_path)

        if researcher_id in seen_ids:
            raise RuntimeError("Duplicate researcher id")
        if wallet_address.lower() in seen_wallets:
            raise RuntimeError("Duplicate researcher walletAddress")
        if token in seen_tokens:
            raise RuntimeError("Researcher tokens must be unique")

        seen_ids.add(researcher_id)
        seen_wallets.add(wallet_address.lower())
        seen_tokens.add(token)

        credentials.append(
            _ResearcherCredential(
                researcher_id=researcher_id,
                wallet_address=wallet_address,
                token=token,
                enabled=enabled,
            )
        )

    return tuple(credentials)


_assert_private_path(AUTH_ROOT, directory=True)
# Credentials are read for each authentication. Removing a token file or
# disabling a researcher takes effect without changing ledger attribution.
_load_token(HOSPITAL_TOKEN_PATH)
_load_researchers()


def authenticated_identity(
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
) -> AuthIdentity:
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Bearer authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, separator, token = authorization.partition(" ")

    if (
        not separator
        or scheme.lower() != "bearer"
        or not token.strip()
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = token.strip()

    hospital_match = hmac.compare_digest(
        token,
        _load_token(HOSPITAL_TOKEN_PATH),
    )

    matched_researcher: _ResearcherCredential | None = None

    # Compare against every configured researcher credential.
    for credential in _load_researchers():
        if hmac.compare_digest(token, credential.token) and credential.enabled:
            matched_researcher = credential

    if hospital_match:
        return AuthIdentity(role="hospital")

    if matched_researcher is not None:
        return AuthIdentity(
            role="researcher",
            researcher_id=matched_researcher.researcher_id,
            wallet_address=matched_researcher.wallet_address,
        )

    raise HTTPException(
        status_code=401,
        detail="Invalid bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def authenticated_role(
    identity: AuthIdentity = Depends(authenticated_identity),
) -> str:
    return identity.role


def require_authenticated(
    identity: AuthIdentity = Depends(authenticated_identity),
) -> AuthIdentity:
    return identity


def require_hospital(
    identity: AuthIdentity = Depends(authenticated_identity),
) -> AuthIdentity:
    if identity.role != "hospital":
        raise HTTPException(
            status_code=403,
            detail="Hospital role required",
        )

    return identity


def require_researcher(
    identity: AuthIdentity = Depends(authenticated_identity),
) -> AuthIdentity:
    if identity.role != "researcher" or identity.wallet_address is None:
        raise HTTPException(
            status_code=403,
            detail="Researcher role required",
        )

    return identity


def researcher_org(identity: AuthIdentity) -> str:
    if identity.role != "researcher" or identity.wallet_address is None:
        raise ValueError("Researcher identity required")

    return f"researcher-address:{identity.wallet_address}"
