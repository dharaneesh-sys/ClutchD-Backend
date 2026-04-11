from fastapi import APIRouter, Query

from app.api.deps import DbSession
from app.services import matching

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("/nearby")
async def nearby_providers(
    db: DbSession,
    lat: float = Query(...),
    lng: float = Query(...),
    issue: str | None = Query(None, description="Optional issue tag for expertise/service overlap"),
):
    mechs = await matching.nearest_mechanics(db, lat, lng, limit=25, issue_tag=issue)
    gars = await matching.nearest_garages(db, lat, lng, limit=25, issue_tag=issue)
    return {
        "mechanics": [matching.mechanic_to_map_dict(m) for m in mechs],
        "garages": [matching.garage_to_map_dict(g) for g in gars],
    }
