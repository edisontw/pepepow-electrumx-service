"""
Broadcast API placeholder.

Do not enable this during the read-only phases. Future support may only accept
signed raw transactions and must never receive mnemonic, seed phrase, or private keys.
"""

from fastapi import APIRouter, HTTPException, status

router = APIRouter(tags=["broadcast"])


@router.post("/wallet/broadcast")
async def broadcast_disabled() -> dict:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Broadcast is disabled. Future support may only accept signed raw transactions.",
    )
