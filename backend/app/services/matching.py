import logging
import math
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# KYC gate for provider discovery (nearby search + offer dispatch). Read per
# call — the flag is env-controlled and can be flipped without a deploy.
def _verified_gate() -> str:
    """SQL fragment for the verification filter: 'm.verified = true' or 'true'.
    Determined by settings.require_verified_providers (default True)."""
    if get_settings().require_verified_providers:
        return "m.verified = true"
    return "true"


def _garage_verified_gate() -> str:
    """Same gate for garage queries (column prefix g.)."""
    if get_settings().require_verified_providers:
        return "g.verified = true"
    return "true"

# When PostGIS is not available (Render managed PostgreSQL, etc.),
# fall back to fetching all records and computing distances in Python.
# Set the max we'll pull for in-memory sorting to avoid OOM.
_MAX_FALLBACK_ROWS = 200

# Mirrors ClutchD-App/src/lib/constants.js ISSUE_TO_EXPERTISE exactly.
# Maps frontend ISSUE_TAGS values to backend expertise/services values.
# Only 2/12 overlap directly (electrical, transmission); this fixes dispatch.
# ``other`` and unknown tags resolve to None (unfiltered dispatch).
ISSUE_TO_EXPERTISE: dict[str, str | None] = {
    "flat_tire": "tires",
    "engine_failure": "engine",
    "battery_dead": "battery",
    "overheating": "engine",
    "brake_issue": "brakes",
    "oil_leak": "oil",
    "electrical": "electrical",
    "ac_not_working": "ac",
    "transmission": "transmission",
    "starting_issue": "engine",
    "noise": "diagnostics",
    "other": None,
}

# Set of valid expertise targets (non-None values of the map) — used to make
# the mapping idempotent when callers pre-map (e.g., offer_service).
_EXPERTISE_VALUES: set[str] = {v for v in ISSUE_TO_EXPERTISE.values() if v is not None}


def issue_tag_to_expertise(tag: str | None) -> str | None:
    """Resolve a frontend issue tag to its expertise value.

    Mirrors ``issueTagToExpertise`` in ClutchD-App/src/lib/constants.js:
    unknown / empty / ``other`` → ``None`` (unfiltered dispatch).
    """
    if not isinstance(tag, str) or not tag:
        return None
    if tag in ISSUE_TO_EXPERTISE:
        return ISSUE_TO_EXPERTISE[tag]
    return None


def _resolve_expertise(tag: str | None) -> str | None:
    """Internal helper: idempotent resolution for filtering.

    Handles both raw issue tags (``flat_tire`` → ``tires``) and already-mapped
    expertise values (``tires`` → ``tires``) so that callers like
    ``offer_service`` which pre-map do not double-map to ``None``.
    Unknown tags → ``None`` (unfiltered).
    """
    if not isinstance(tag, str) or not tag:
        return None
    if tag in ISSUE_TO_EXPERTISE:
        return ISSUE_TO_EXPERTISE[tag]
    if tag in _EXPERTISE_VALUES:
        return tag
    return None

def _coerce_uuid(value: Any) -> UUID:
    """Normalize a raw SQL row id to ``UUID``.

    asyncpg (production) returns native UUIDs; SQLite ``text()`` rows return
    undashed hex strings. Keeps the ``Ranked*`` dataclass contracts
    dialect-independent. No-op on PostgreSQL.
    """
    return value if isinstance(value, UUID) else UUID(str(value))


def _coerce_str_list(value: Any) -> list[str]:
    """Normalize an ARRAY/JSON column value to ``list[str]`` across dialects.

    SQLite renders ARRAY(String) as JSON text and ``text()`` rows bypass
    result processors, so values can arrive as raw strings.
    """
    if isinstance(value, str):
        import json as _json

        try:
            parsed = _json.loads(value)
        except Exception:
            return []
        value = parsed
    return [str(v) for v in (value or [])]
_settings = get_settings()


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


async def _postgis_fetch(db: AsyncSession, sql: str, params: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Execute a PostGIS query.

    Returns rows (possibly empty) on success, or ``None`` when the query
    fails so callers can distinguish "PostGIS is down" (fall back) from
    "no providers matched" (an empty result — do not fall back).
    """
    try:
        result = await db.execute(text(sql), params)
        return result.mappings().all()
    except Exception as exc:
        logger.warning(
            "PostGIS query failed, falling back to Python haversine: %s", exc,
            exc_info=True,
        )
        return None


async def _fallback_mechanics(
    db: AsyncSession,
    lat: float,
    lon: float,
    limit: int,
    issue_tag: str | None,
) -> list[RankedMechanic]:
    expertise = _resolve_expertise(issue_tag)
    if expertise:
        q = text(f"""
            SELECT m.id, m.full_name, m.lat, m.lon, m.rating, m.expertise
            FROM mechanics m
            JOIN users u ON u.id = m.user_id
            WHERE {_verified_gate()} AND m.available = true
              AND m.penalized = false
              AND u.is_active = true
              AND m.expertise && ARRAY[CAST(:tag AS VARCHAR)]
            ORDER BY u.created_at ASC, u.id ASC
            LIMIT :maxrows
        """)
        params: dict[str, Any] = {"maxrows": _MAX_FALLBACK_ROWS, "tag": expertise}
        try:
            result = await db.execute(q, params)
            rows = result.mappings().all()
        except Exception:
            q_all = text(f"""
                SELECT m.id, m.full_name, m.lat, m.lon, m.rating, m.expertise
                FROM mechanics m
                JOIN users u ON u.id = m.user_id
                WHERE {_verified_gate()} AND m.available = true
                  AND m.penalized = false
                  AND u.is_active = true
                ORDER BY u.created_at ASC, u.id ASC
                LIMIT :maxrows
            """)
            result = await db.execute(q_all, {"maxrows": _MAX_FALLBACK_ROWS})
            all_rows = result.mappings().all()
            rows = []
            for r in all_rows:
                exp = r["expertise"]
                if isinstance(exp, str):
                    import json as _json
                    try:
                        exp = _json.loads(exp)
                    except Exception:
                        exp = []
                if expertise in (exp or []):
                    rows.append(r)
    else:
        q = text(f"""
            SELECT m.id, m.full_name, m.lat, m.lon, m.rating, m.expertise
            FROM mechanics m
            JOIN users u ON u.id = m.user_id
            WHERE {_verified_gate()} AND m.available = true
              AND m.penalized = false
              AND u.is_active = true
            ORDER BY u.created_at ASC, u.id ASC
            LIMIT :maxrows
        """)
        params: dict[str, Any] = {"maxrows": _MAX_FALLBACK_ROWS}
        result = await db.execute(q, params)
        rows = result.mappings().all()

    ranked: list[RankedMechanic] = []
    for row in rows:
        dist_m = haversine_m(lat, lon, float(row["lat"]), float(row["lon"]))
        if dist_m > _settings.search_radius_m:
            continue
        rating = float(row["rating"] or 0)
        ranked.append(
            RankedMechanic(
                id=_coerce_uuid(row["id"]),
                full_name=row["full_name"],
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                rating=rating,
                distance_m=dist_m,
                score=_score(dist_m, rating, 0.5),
                expertise=_coerce_str_list(row["expertise"]),
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
    expertise = _resolve_expertise(issue_tag)
    if expertise:
        q = text(f"""
            SELECT g.id, g.garage_name, g.lat, g.lon, g.rating, g.services
            FROM garages g
            JOIN users u ON u.id = g.user_id
            WHERE {_garage_verified_gate()}
              AND g.penalized = false
              AND u.is_active = true
              AND g.services && ARRAY[CAST(:tag AS VARCHAR)]
            ORDER BY u.created_at ASC, u.id ASC
            LIMIT :maxrows
        """)
        params: dict[str, Any] = {"maxrows": _MAX_FALLBACK_ROWS, "tag": expertise}
        try:
            result = await db.execute(q, params)
            rows = result.mappings().all()
        except Exception:
            q_all = text(f"""
                SELECT g.id, g.garage_name, g.lat, g.lon, g.rating, g.services
                FROM garages g
                JOIN users u ON u.id = g.user_id
                WHERE {_garage_verified_gate()}
                  AND g.penalized = false
                  AND u.is_active = true
                ORDER BY u.created_at ASC, u.id ASC
                LIMIT :maxrows
            """)
            result = await db.execute(q_all, {"maxrows": _MAX_FALLBACK_ROWS})
            all_rows = result.mappings().all()
            rows = []
            for r in all_rows:
                svc = r["services"]
                if isinstance(svc, str):
                    import json as _json
                    try:
                        svc = _json.loads(svc)
                    except Exception:
                        svc = []
                if expertise in (svc or []):
                    rows.append(r)
    else:
        q = text(f"""
            SELECT g.id, g.garage_name, g.lat, g.lon, g.rating, g.services
            FROM garages g
            JOIN users u ON u.id = g.user_id
            WHERE {_garage_verified_gate()}
              AND g.penalized = false
              AND u.is_active = true
            ORDER BY u.created_at ASC, u.id ASC
            LIMIT :maxrows
        """)
        params: dict[str, Any] = {"maxrows": _MAX_FALLBACK_ROWS}
        result = await db.execute(q, params)
        rows = result.mappings().all()

    ranked: list[RankedGarage] = []
    for row in rows:
        dist_m = haversine_m(lat, lon, float(row["lat"]), float(row["lon"]))
        if dist_m > _settings.search_radius_m:
            continue
        rating = float(row["rating"] or 0)
        ranked.append(
            RankedGarage(
                id=_coerce_uuid(row["id"]),
                garage_name=row["garage_name"],
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                rating=rating,
                distance_m=dist_m,
                score=_score(dist_m, rating, 0.3),
                services=_coerce_str_list(row["services"]),
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
    sql_issue = f"""
        SELECT m.id, m.full_name, m.lat, m.lon, m.rating, m.expertise,
          ST_Distance(
            geography(ST_SetSRID(ST_MakePoint(m.lon, m.lat), 4326)),
            geography(ST_SetSRID(ST_MakePoint(:ulon, :ulat), 4326))
          ) AS dist_m
        FROM mechanics m
        JOIN users u ON u.id = m.user_id
        WHERE {_verified_gate()} AND m.available = true
          AND m.penalized = false
          AND u.is_active = true
          AND m.expertise && ARRAY[CAST(:tag AS VARCHAR)]
          AND ST_DWithin(
                geography(ST_SetSRID(ST_MakePoint(m.lon, m.lat), 4326)),
                geography(ST_SetSRID(ST_MakePoint(:ulon, :ulat), 4326)),
                :radius_m
              )
        ORDER BY dist_m ASC
        LIMIT :limit
    """
    sql_all = f"""
        SELECT m.id, m.full_name, m.lat, m.lon, m.rating, m.expertise,
          ST_Distance(
            geography(ST_SetSRID(ST_MakePoint(m.lon, m.lat), 4326)),
            geography(ST_SetSRID(ST_MakePoint(:ulon, :ulat), 4326))
          ) AS dist_m
        FROM mechanics m
        JOIN users u ON u.id = m.user_id
        WHERE {_verified_gate()} AND m.available = true
          AND m.penalized = false
          AND u.is_active = true
          AND ST_DWithin(
                geography(ST_SetSRID(ST_MakePoint(m.lon, m.lat), 4326)),
                geography(ST_SetSRID(ST_MakePoint(:ulon, :ulat), 4326)),
                :radius_m
              )
        ORDER BY dist_m ASC
        LIMIT :limit
    """
    expertise = _resolve_expertise(issue_tag)
    if expertise:
        params: dict[str, Any] = {"ulat": lat, "ulon": lon, "limit": limit, "tag": expertise, "radius_m": _settings.search_radius_m}
    else:
        params = {"ulat": lat, "ulon": lon, "limit": limit, "radius_m": _settings.search_radius_m}

    sql = sql_issue if expertise else sql_all
    rows = await _postgis_fetch(db, sql, params)
    if rows is not None:
        ranked: list[RankedMechanic] = []
        for row in rows:
            dist_m = float(row["dist_m"])
            rating = float(row["rating"] or 0)
            ranked.append(
                RankedMechanic(
                    id=_coerce_uuid(row["id"]),
                    full_name=row["full_name"],
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                    rating=rating,
                    distance_m=dist_m,
                    score=_score(dist_m, rating, 0.5),
                    expertise=_coerce_str_list(row["expertise"]),
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
    sql_issue = f"""
        SELECT g.id, g.garage_name, g.lat, g.lon, g.rating, g.services,
          ST_Distance(
            geography(ST_SetSRID(ST_MakePoint(g.lon, g.lat), 4326)),
            geography(ST_SetSRID(ST_MakePoint(:ulon, :ulat), 4326))
          ) AS dist_m
        FROM garages g
        JOIN users u ON u.id = g.user_id
        WHERE {_garage_verified_gate()}
          AND g.penalized = false
          AND u.is_active = true
          AND g.services && ARRAY[CAST(:tag AS VARCHAR)]
          AND ST_DWithin(
                geography(ST_SetSRID(ST_MakePoint(g.lon, g.lat), 4326)),
                geography(ST_SetSRID(ST_MakePoint(:ulon, :ulat), 4326)),
                :radius_m
              )
        ORDER BY dist_m ASC
        LIMIT :limit
    """
    sql_all = f"""
        SELECT g.id, g.garage_name, g.lat, g.lon, g.rating, g.services,
          ST_Distance(
            geography(ST_SetSRID(ST_MakePoint(g.lon, g.lat), 4326)),
            geography(ST_SetSRID(ST_MakePoint(:ulon, :ulat), 4326))
          ) AS dist_m
        FROM garages g
        JOIN users u ON u.id = g.user_id
        WHERE {_garage_verified_gate()}
          AND g.penalized = false
          AND u.is_active = true
          AND ST_DWithin(
                geography(ST_SetSRID(ST_MakePoint(g.lon, g.lat), 4326)),
                geography(ST_SetSRID(ST_MakePoint(:ulon, :ulat), 4326)),
                :radius_m
              )
        ORDER BY dist_m ASC
        LIMIT :limit
    """
    expertise = _resolve_expertise(issue_tag)
    if expertise:
        params: dict[str, Any] = {"ulat": lat, "ulon": lon, "limit": limit, "tag": expertise, "radius_m": _settings.search_radius_m}
    else:
        params = {"ulat": lat, "ulon": lon, "limit": limit, "radius_m": _settings.search_radius_m}

    sql = sql_issue if expertise else sql_all
    rows = await _postgis_fetch(db, sql, params)
    if rows is not None:
        ranked: list[RankedGarage] = []
        for row in rows:
            dist_m = float(row["dist_m"])
            rating = float(row["rating"] or 0)
            ranked.append(
                RankedGarage(
                    id=_coerce_uuid(row["id"]),
                    garage_name=row["garage_name"],
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                    rating=rating,
                    distance_m=dist_m,
                    score=_score(dist_m, rating, 0.3),
                    services=_coerce_str_list(row["services"]),
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
        # 0 = no reviews yet → frontend shows "New" instead of a star rating.
        "rating": round(m.rating, 1) if m.rating else None,
        "expertise": m.expertise,
        "distanceKm": round(m.distance_m / 1000.0, 2),
    }


def garage_to_map_dict(g: RankedGarage) -> dict:
    return {
        "id": str(g.id),
        "name": g.garage_name,
        "location": [g.lat, g.lon],
        "rating": round(g.rating, 1) if g.rating else None,
        "services": _coerce_str_list(g.services),
        "distanceKm": round(g.distance_m / 1000.0, 2),
    }
