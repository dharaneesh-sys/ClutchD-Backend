# DECISION NOTE (task 16, clutchd-app-hardening, 2026-08-23): KEPT, not deleted.
# Registered in app/api/v1/router.py:9 (api_router.include_router(matching_routes.router)),
# so removal would be a live-API change. button-audit.md (commit 12a6fa7, rows S2/S6 +
# dead-endpoint table) documents both endpoints as intentional-dead/unlinked: the frontend
# uses GET /providers/nearby?lat&lng (providers.py) exclusively, and these routes take
# 'lon' where providers.py takes 'lng'. Reachable but never called from the UI.
# If a purge is ever approved: delete this file AND router.py line 9 import+include together.
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
