import logging
import math
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# When PostGIS is not available (Render managed PostgreSQL, etc.),
# fall back to fetching all records and computing distances in Python.
# Set the max we'll pull for in-memory sorting to avoid OOM.
_MAX_FALLBACK_ROWS = 2000


@dataclass
class RankedMechanic:
    id: UUID
    full_name: str
    lat: float
    lon: float
    rating: float
    distance_m: float
    score: float
    expertise: list[str]


@dataclass
class RankedGarage:
    id: UUID
    garage_name: str
    lat: float
    lon: float
    rating: float
    distance_m: float
    score: float
    services: list[str]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def _score(distance_m: float, rating: float, availability_bonus: float = 0.0) -> float:
    dist_km = max(distance_m / 1000.0, 0.05)
    return (rating or 3.0) * 2.0 - dist_km * 0.8 + availability_bonus


async def _postgis_fetch(db: AsyncSession, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Execute a PostGIS query; return rows or None on failure."""
    try:
        result = await db.execute(text(sql), params)
        return result.mappings().all()
    except Exception as exc:
        logger.warning("PostGIS query failed, falling back to Python haversine: %s", exc)
        return []


async def _fallback_mechanics(
    db: AsyncSession,
    lat: float,
    lon: float,
    limit: int,
    issue_tag: str | None,
) -> list[RankedMechanic]:
    q = text("""
        SELECT id, full_name, lat, lon, rating, expertise
        FROM mechanics
        WHERE verified = true AND available = true
        {tag_filter}
        LIMIT :maxrows
    """.format(
        tag_filter="AND expertise && ARRAY[CAST(:tag AS VARCHAR)]" if issue_tag else ""
    ))
    params: dict[str, Any] = {"maxrows": _MAX_FALLBACK_ROWS}
    if issue_tag:
        params["tag"] = issue_tag
    result = await db.execute(q, params)
    rows = result.mappings().all()

    ranked: list[RankedMechanic] = []
    for row in rows:
        dist_m = haversine_m(lat, lon, float(row["lat"]), float(row["lon"]))
        rating = float(row["rating"] or 0)
        ranked.append(
            RankedMechanic(
                id=row["id"],
                full_name=row["full_name"],
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                rating=rating,
                distance_m=dist_m,
                score=_score(dist_m, rating, 0.5),
                expertise=list(row["expertise"] or []),
            )
        )
    ranked.sort(key=lambda x: -x.score)
    return ranked[:limit]


async def _fallback_garages(
    db: AsyncSession,
    lat: float,
    lon: float,
    limit: int,
    issue_tag: str | None,
) -> list[RankedGarage]:
    q = text("""
        SELECT id, garage_name, lat, lon, rating, services
        FROM garages
        WHERE verified = true
        {tag_filter}
        LIMIT :maxrows
    """.format(
        tag_filter="AND services && ARRAY[CAST(:tag AS VARCHAR)]" if issue_tag else ""
    ))
    params: dict[str, Any] = {"maxrows": _MAX_FALLBACK_ROWS}
    if issue_tag:
        params["tag"] = issue_tag
    result = await db.execute(q, params)
    rows = result.mappings().all()

    ranked: list[RankedGarage] = []
    for row in rows:
        dist_m = haversine_m(lat, lon, float(row["lat"]), float(row["lon"]))
        rating = float(row["rating"] or 0)
        ranked.append(
            RankedGarage(
                id=row["id"],
                garage_name=row["garage_name"],
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                rating=rating,
                distance_m=dist_m,
                score=_score(dist_m, rating, 0.3),
                services=list(row["services"] or []),
            )
        )
    ranked.sort(key=lambda x: -x.score)
    return ranked[:limit]


async def nearest_mechanics(
    db: AsyncSession,
    lat: float,
    lon: float,
    limit: int = 20,
    issue_tag: str | None = None,
) -> list[RankedMechanic]:
    sql_issue = """
        SELECT id, full_name, lat, lon, rating, expertise,
          ST_Distance(
            geography(ST_SetSRID(ST_MakePoint(lon, lat), 4326)),
            geography(ST_SetSRID(ST_MakePoint(:ulon, :ulat), 4326))
          ) AS dist_m
        FROM mechanics
        WHERE verified = true AND available = true
          AND expertise && ARRAY[CAST(:tag AS VARCHAR)]
        ORDER BY dist_m ASC
        LIMIT :limit
    """
    sql_all = """
        SELECT id, full_name, lat, lon, rating, expertise,
          ST_Distance(
            geography(ST_SetSRID(ST_MakePoint(lon, lat), 4326)),
            geography(ST_SetSRID(ST_MakePoint(:ulon, :ulat), 4326))
          ) AS dist_m
        FROM mechanics
        WHERE verified = true AND available = true
        ORDER BY dist_m ASC
        LIMIT :limit
    """
    if issue_tag:
        params: dict[str, Any] = {"ulat": lat, "ulon": lon, "limit": limit, "tag": issue_tag}
    else:
        params = {"ulat": lat, "ulon": lon, "limit": limit}

    sql = sql_issue if issue_tag else sql_all
    rows = await _postgis_fetch(db, sql, params)
    if rows:
        ranked: list[RankedMechanic] = []
        for row in rows:
            dist_m = float(row["dist_m"])
            rating = float(row["rating"] or 0)
            ranked.append(
                RankedMechanic(
                    id=row["id"],
                    full_name=row["full_name"],
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                    rating=rating,
                    distance_m=dist_m,
                    score=_score(dist_m, rating, 0.5),
                    expertise=list(row["expertise"] or []),
                )
            )
        ranked.sort(key=lambda x: -x.score)
        return ranked

    return await _fallback_mechanics(db, lat, lon, limit, issue_tag)


async def nearest_garages(
    db: AsyncSession,
    lat: float,
    lon: float,
    limit: int = 20,
    issue_tag: str | None = None,
) -> list[RankedGarage]:
    sql_issue = """
        SELECT id, garage_name, lat, lon, rating, services,
          ST_Distance(
            geography(ST_SetSRID(ST_MakePoint(lon, lat), 4326)),
            geography(ST_SetSRID(ST_MakePoint(:ulon, :ulat), 4326))
          ) AS dist_m
        FROM garages
        WHERE verified = true
          AND services && ARRAY[CAST(:tag AS VARCHAR)]
        ORDER BY dist_m ASC
        LIMIT :limit
    """
    sql_all = """
        SELECT id, garage_name, lat, lon, rating, services,
          ST_Distance(
            geography(ST_SetSRID(ST_MakePoint(lon, lat), 4326)),
            geography(ST_SetSRID(ST_MakePoint(:ulon, :ulat), 4326))
          ) AS dist_m
        FROM garages
        WHERE verified = true
        ORDER BY dist_m ASC
        LIMIT :limit
    """
    if issue_tag:
        params: dict[str, Any] = {"ulat": lat, "ulon": lon, "limit": limit, "tag": issue_tag}
    else:
        params = {"ulat": lat, "ulon": lon, "limit": limit}

    sql = sql_issue if issue_tag else sql_all
    rows = await _postgis_fetch(db, sql, params)
    if rows:
        ranked: list[RankedGarage] = []
        for row in rows:
            dist_m = float(row["dist_m"])
            rating = float(row["rating"] or 0)
            ranked.append(
                RankedGarage(
                    id=row["id"],
                    garage_name=row["garage_name"],
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                    rating=rating,
                    distance_m=dist_m,
                    score=_score(dist_m, rating, 0.3),
                    services=list(row["services"] or []),
                )
            )
        ranked.sort(key=lambda x: -x.score)
        return ranked

    return await _fallback_garages(db, lat, lon, limit, issue_tag)


def mechanic_to_map_dict(m: RankedMechanic) -> dict:
    return {
        "id": str(m.id),
        "name": m.full_name,
        "location": [m.lat, m.lon],
        "rating": round(m.rating, 1),
        "expertise": m.expertise,
        "distanceKm": round(m.distance_m / 1000.0, 2),
    }


def garage_to_map_dict(g: RankedGarage) -> dict:
    return {
        "id": str(g.id),
        "name": g.garage_name,
        "location": [g.lat, g.lon],
        "rating": round(g.rating, 1),
        "distanceKm": round(g.distance_m / 1000.0, 2),
    }
