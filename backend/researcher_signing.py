from __future__ import annotations

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import HTTPException

from backend.api_auth import AuthIdentity


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
