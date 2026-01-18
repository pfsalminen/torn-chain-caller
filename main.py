import json
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, get_session, async_session, Faction, ChainClaim
from torn_api import validate_api_key, get_chain_info, TornAPIError, TornUser, ChainInfo


# WebSocket connection manager per faction
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[int, list[WebSocket]] = defaultdict(list)

    async def connect(self, websocket: WebSocket, faction_id: int):
        await websocket.accept()
        self.active_connections[faction_id].append(websocket)

    def disconnect(self, websocket: WebSocket, faction_id: int):
        if websocket in self.active_connections[faction_id]:
            self.active_connections[faction_id].remove(websocket)

    async def broadcast(self, faction_id: int, message: dict):
        for connection in self.active_connections[faction_id]:
            try:
                await connection.send_json(message)
            except Exception:
                pass  # Connection might be closed


manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Chain Claim System", lifespan=lifespan)


# Pydantic models
class AuthResponse(BaseModel):
    user_id: int
    name: str
    faction_id: Optional[int]
    faction_name: Optional[str]


class ClaimRequest(BaseModel):
    start_hit: int
    end_hit: Optional[int] = None  # If provided, creates claims for range start_hit to end_hit


class ClaimResponse(BaseModel):
    id: int
    faction_id: int
    hit_number: int
    claimed_by: int
    claimed_by_name: str
    claimed_at: str


class ChainInfoResponse(BaseModel):
    current: int
    max: int
    timeout: int
    cooldown: int


# Auth dependency
async def get_current_user(x_api_key: str = Header(...)) -> TornUser:
    try:
        user = await validate_api_key(x_api_key)
        if user.faction_id is None:
            raise HTTPException(status_code=403, detail="You must be in a faction to use this app")
        return user
    except TornAPIError as e:
        raise HTTPException(status_code=401, detail=str(e))


async def ensure_faction_exists(session: AsyncSession, faction_id: int, faction_name: str):
    """Ensure the faction exists in the database, create if not."""
    result = await session.execute(select(Faction).where(Faction.id == faction_id))
    faction = result.scalar_one_or_none()
    if faction is None:
        faction = Faction(id=faction_id, name=faction_name)
        session.add(faction)
        await session.commit()
    elif faction.name != faction_name:
        faction.name = faction_name
        await session.commit()


# Routes
@app.post("/api/auth", response_model=AuthResponse)
async def authenticate(
    x_api_key: str = Header(...),
    session: AsyncSession = Depends(get_session)
):
    """Validate API key and return user/faction info."""
    try:
        user = await validate_api_key(x_api_key)
    except TornAPIError as e:
        raise HTTPException(status_code=401, detail=str(e))

    if user.faction_id is None:
        raise HTTPException(status_code=403, detail="You must be in a faction to use this app")

    await ensure_faction_exists(session, user.faction_id, user.faction_name)

    return AuthResponse(
        user_id=user.user_id,
        name=user.name,
        faction_id=user.faction_id,
        faction_name=user.faction_name,
    )


@app.get("/api/claims", response_model=list[ClaimResponse])
async def get_claims(
    user: TornUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session)
):
    """Get all claims for the user's faction."""
    result = await session.execute(
        select(ChainClaim)
        .where(ChainClaim.faction_id == user.faction_id)
        .order_by(ChainClaim.hit_number)
    )
    claims = result.scalars().all()
    return [
        ClaimResponse(
            id=c.id,
            faction_id=c.faction_id,
            hit_number=c.hit_number,
            claimed_by=c.claimed_by,
            claimed_by_name=c.claimed_by_name,
            claimed_at=c.claimed_at.isoformat() if c.claimed_at else "",
        )
        for c in claims
    ]


@app.post("/api/claims", response_model=list[ClaimResponse])
async def create_claim(
    claim: ClaimRequest,
    user: TornUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session)
):
    """Add new claims (single hit or range expanded to individual claims)."""
    if claim.start_hit < 1:
        raise HTTPException(status_code=400, detail="Hit number must be positive")
    if claim.end_hit is not None and claim.end_hit < claim.start_hit:
        raise HTTPException(status_code=400, detail="End hit must be >= start hit")

    # Determine the range of hits to claim
    end = claim.end_hit if claim.end_hit is not None else claim.start_hit
    hit_numbers = list(range(claim.start_hit, end + 1))

    # Limit range size to prevent abuse
    if len(hit_numbers) > 50:
        raise HTTPException(status_code=400, detail="Cannot claim more than 50 hits at once")

    await ensure_faction_exists(session, user.faction_id, user.faction_name)

    # Check which numbers are already claimed
    result = await session.execute(
        select(ChainClaim.hit_number)
        .where(ChainClaim.faction_id == user.faction_id)
        .where(ChainClaim.hit_number.in_(hit_numbers))
    )
    already_claimed = {row[0] for row in result.fetchall()}

    if already_claimed:
        raise HTTPException(
            status_code=409,
            detail=f"Hit(s) already claimed: {', '.join(map(str, sorted(already_claimed)))}"
        )

    # Create individual claims for each hit number
    created_claims = []
    for hit_num in hit_numbers:
        new_claim = ChainClaim(
            faction_id=user.faction_id,
            hit_number=hit_num,
            claimed_by=user.user_id,
            claimed_by_name=user.name,
        )
        session.add(new_claim)
        created_claims.append(new_claim)

    await session.commit()

    # Refresh all claims to get their IDs
    for c in created_claims:
        await session.refresh(c)

    responses = [
        ClaimResponse(
            id=c.id,
            faction_id=c.faction_id,
            hit_number=c.hit_number,
            claimed_by=c.claimed_by,
            claimed_by_name=c.claimed_by_name,
            claimed_at=c.claimed_at.isoformat() if c.claimed_at else "",
        )
        for c in created_claims
    ]

    # Broadcast each claim to faction
    for response in responses:
        await manager.broadcast(user.faction_id, {
            "type": "claim_added",
            "claim": response.model_dump()
        })

    return responses


@app.delete("/api/claims/{claim_id}")
async def delete_claim(
    claim_id: int,
    user: TornUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session)
):
    """Delete a claim (only own claims)."""
    result = await session.execute(
        select(ChainClaim).where(ChainClaim.id == claim_id)
    )
    claim = result.scalar_one_or_none()

    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")

    if claim.faction_id != user.faction_id:
        raise HTTPException(status_code=403, detail="Claim belongs to another faction")

    if claim.claimed_by != user.user_id:
        raise HTTPException(status_code=403, detail="You can only delete your own claims")

    await session.execute(delete(ChainClaim).where(ChainClaim.id == claim_id))
    await session.commit()

    # Broadcast to faction
    await manager.broadcast(user.faction_id, {
        "type": "claim_deleted",
        "claim_id": claim_id
    })

    return {"status": "deleted"}


@app.get("/api/chain", response_model=ChainInfoResponse)
async def get_chain(
    x_api_key: str = Header(...),
    user: TornUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session)
):
    """Get current chain info and auto-delete claims below current chain count."""
    try:
        chain_info = await get_chain_info(x_api_key)
    except TornAPIError as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch chain info: {e}")

    # Delete claims that are at or below the current chain count
    if chain_info.current > 0:
        result = await session.execute(
            select(ChainClaim)
            .where(ChainClaim.faction_id == user.faction_id)
            .where(ChainClaim.hit_number <= chain_info.current)
        )
        old_claims = result.scalars().all()

        deleted_ids = [c.id for c in old_claims]

        if deleted_ids:
            await session.execute(
                delete(ChainClaim)
                .where(ChainClaim.faction_id == user.faction_id)
                .where(ChainClaim.hit_number <= chain_info.current)
            )
            await session.commit()

            # Broadcast deletions to faction
            for claim_id in deleted_ids:
                await manager.broadcast(user.faction_id, {
                    "type": "claim_deleted",
                    "claim_id": claim_id
                })

    return ChainInfoResponse(
        current=chain_info.current,
        max=chain_info.max,
        timeout=chain_info.timeout,
        cooldown=chain_info.cooldown,
    )


@app.websocket("/ws/{faction_id}")
async def websocket_endpoint(websocket: WebSocket, faction_id: int):
    """WebSocket endpoint for real-time updates."""
    await manager.connect(websocket, faction_id)
    try:
        while True:
            # Keep connection alive, we only broadcast from server
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, faction_id)


# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
