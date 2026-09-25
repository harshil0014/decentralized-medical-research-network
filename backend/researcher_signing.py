from __future__ import annotations

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import HTTPException

from backend.api_auth import AuthIdentity

_SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def verify_researcher_signature(
    identity: AuthIdentity,
    digest: str,
    signature: str,
) -> None:
    expected = (identity.wallet_address or "").lower()
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="External researcher wallet is not configured",
        )

    try:
        raw = bytes.fromhex(signature.removeprefix("0x"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid researcher wallet signature") from exc
    if len(raw) != 65:
        raise HTTPException(status_code=400, detail="Invalid researcher wallet signature")
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:64], "big")
    if not (0 < r < _SECP256K1_ORDER and 0 < s <= _SECP256K1_ORDER // 2):
        raise HTTPException(status_code=400, detail="Noncanonical researcher wallet signature")
    if raw[64] not in (0, 1, 27, 28):
        raise HTTPException(status_code=400, detail="Invalid researcher wallet signature")

    try:
        recovered = Account.recover_message(
            encode_defunct(hexstr=digest),
            signature=signature,
        ).lower()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid researcher wallet signature",
        ) from exc

    if recovered != expected:
        raise HTTPException(
            status_code=403,
            detail="Signature does not match the authenticated researcher wallet",
        )
