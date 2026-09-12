import logging
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.limiter import limiter
from app.models.fleet import Fleet
from app.models.user import User
from app.schemas.fleet import FleetListResponse, FleetRegisterBody, FleetResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fleet", tags=["fleet"])

# GSTIN format: 2-digit state code + 10-char PAN + entity code + Z + checksum
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")


def _to_response(fleet: Fleet) -> dict:
    return {
        "id": fleet.id,
        "companyName": fleet.company_name,
        "fleetType": fleet.fleet_type,
        "fleetSize": fleet.fleet_size,
        "contactName": fleet.contact_name,
        "contactEmail": fleet.contact_email,
        "contactPhone": fleet.contact_phone,
        "businessAddress": fleet.business_address,
        "gstin": fleet.gstin,
        "tier": fleet.tier,
        "discountRate": fleet.discount_rate,
        "priorityDispatch": fleet.priority_dispatch,
        "totalJobsCompleted": fleet.total_jobs_completed,
        "totalSpent": fleet.total_spent,
        "registeredAt": fleet.created_at,
    }


@router.post("/register", response_model=FleetResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register_fleet(request: Request, body: FleetRegisterBody, db: DbSession):
    """Register a B2B fleet account. Public — visitors can apply before
    creating an app account; if a signed-in user matches the contact email,
    the fleet is linked to their account."""
    gstin = (body.gstin or "").strip().upper() or None
    if gstin and not GSTIN_RE.match(gstin):
        raise HTTPException(status_code=422, detail="Invalid GSTIN format")

    # Dedupe: same company + email may only register once
    existing = await db.execute(
        select(Fleet).where(
            Fleet.contact_email == body.contactEmail.lower(),
            Fleet.company_name == body.companyName.strip(),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="This fleet is already registered")

    # Link to an existing user when the contact email matches an account
    user_row = await db.execute(select(User).where(User.email == body.contactEmail.lower()))
    owner = user_row.scalar_one_or_none()

    fleet = Fleet(
        user_id=owner.id if owner else None,
        company_name=body.companyName.strip(),
        fleet_type=body.fleetType,
        fleet_size=body.fleetSize,
        contact_name=body.contactName.strip(),
        contact_email=body.contactEmail.lower(),
        contact_phone=body.contactPhone.strip(),
        business_address=body.businessAddress.strip(),
        gstin=gstin,
    )
    db.add(fleet)
    await db.flush()
    return _to_response(fleet)


@router.get("/my-fleet", response_model=FleetListResponse)
async def my_fleet(db: DbSession, user: CurrentUser):
    """Fleet registrations linked to the signed-in user (matched by user_id
    or contact email)."""
    r = await db.execute(
        select(Fleet).where(
            (Fleet.user_id == user.id) | (Fleet.contact_email == user.email)
        )
    )
    fleets = r.scalars().all()
    return {"fleets": [_to_response(f) for f in fleets], "total": len(fleets)}


@router.get("/{fleet_id}", response_model=FleetResponse)
async def get_fleet(fleet_id: UUID, db: DbSession, user: CurrentUser):
    """Fetch one fleet registration — owner or admin only."""
    r = await db.execute(select(Fleet).where(Fleet.id == fleet_id))
    fleet = r.scalar_one_or_none()
    if not fleet:
        raise HTTPException(status_code=404, detail="Fleet not found")
    is_owner = fleet.user_id == user.id or fleet.contact_email == user.email
    if not (is_owner or user.role == "admin"):
        raise HTTPException(status_code=403, detail="Not your fleet registration")
    return _to_response(fleet)
