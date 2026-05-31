from fastapi import APIRouter, Query, Request

from app.api.deps import DbSession
from app.core.limiter import limiter
from app.services import matching

router = APIRouter(tags=["matching"])


@router.get("/mechanics/nearby")
@limiter.limit("30/minute")
async def mechanics_nearby(
    request: Request,
    db: DbSession,
    lat: float = Query(...),
    lon: float = Query(...),
    issue: str | None = None,
):
    rows = await matching.nearest_mechanics(db, lat, lon, limit=30, issue_tag=issue)
    return {"results": [matching.mechanic_to_map_dict(m) for m in rows]}


@router.get("/garages/nearby")
@limiter.limit("30/minute")
async def garages_nearby(
    request: Request,
    db: DbSession,
    lat: float = Query(...),
    lon: float = Query(...),
    issue: str | None = None,
):
    rows = await matching.nearest_garages(db, lat, lon, limit=30, issue_tag=issue)
    return {"results": [matching.garage_to_map_dict(g) for g in rows]}
