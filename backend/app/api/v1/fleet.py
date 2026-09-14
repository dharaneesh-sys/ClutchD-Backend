import logging
import re
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.limiter import limiter
from app.models.fleet import Fleet, FleetBooking
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


# ── Fleet bulk bookings ─────────────────────────────────────────────────


class FleetBookingVehicleIn(BaseModel):
    vehicleId: str | None = None
    vehicleName: str = Field(..., max_length=255)
    serviceType: str = Field(..., max_length=64)


class FleetBookingCreate(BaseModel):
    scheduledAt: datetime
    vehicles: list[FleetBookingVehicleIn] = Field(..., min_length=1, max_length=100)
    subtotal: float = Field(0, ge=0)
    discountPercent: int = Field(0, ge=0, le=100)
    total: float = Field(0, ge=0)


class FleetBookingResponse(BaseModel):
    id: UUID
    fleetId: UUID
    scheduledAt: datetime
    vehicleCount: int
    vehicles: list
    subtotal: float
    discountPercent: int
    total: float
    status: str
    createdAt: datetime

    class Config:
        from_attributes = True


def _booking_to_response(b: FleetBooking) -> dict:
    return {
        "id": b.id,
        "fleetId": b.fleet_id,
        "scheduledAt": b.scheduled_at,
        "vehicleCount": b.vehicle_count,
        "vehicles": b.vehicles or [],
        "subtotal": b.subtotal,
        "discountPercent": b.discount_percent,
        "total": b.total,
        "status": b.status,
        "createdAt": b.created_at,
    }


async def _get_own_fleet(db, user: User) -> Fleet:
    r = await db.execute(
        select(Fleet).where(
            (Fleet.user_id == user.id) | (Fleet.contact_email == user.email)
        )
    )
    fleet = r.scalars().first()
    if not fleet:
        raise HTTPException(status_code=404, detail="No fleet registration found — register your fleet first")
    return fleet


@router.post("/bookings", response_model=FleetBookingResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_fleet_booking(request: Request, body: FleetBookingCreate, db: DbSession, user: CurrentUser):
    fleet = await _get_own_fleet(db, user)
    booking = FleetBooking(
        id=uuid4(),
        fleet_id=fleet.id,
        user_id=user.id,
        scheduled_at=body.scheduledAt,
        vehicle_count=len(body.vehicles),
        vehicles=[v.model_dump() for v in body.vehicles],
        subtotal=body.subtotal,
        discount_percent=body.discountPercent,
        total=body.total,
        status="confirmed",
    )
    db.add(booking)
    await db.flush()
    await db.refresh(booking)
    return _booking_to_response(booking)


@router.get("/bookings", response_model=list[FleetBookingResponse])
async def list_fleet_bookings(db: DbSession, user: CurrentUser):
    fleet = await _get_own_fleet(db, user)
    r = await db.execute(
        select(FleetBooking)
        .where(FleetBooking.fleet_id == fleet.id)
        .order_by(FleetBooking.created_at.desc())
    )
    return [_booking_to_response(b) for b in r.scalars().all()]


@router.get("/{fleet_id}", response_model=FleetResponse)
async def get_fleet(fleet_id: UUID, db: DbSession, user: CurrentUser):
    """Fetch one fleet registration — owner or admin only.

    NOTE: must be declared AFTER /bookings so "bookings" is not captured
    as a fleet_id UUID path param.
    """
    r = await db.execute(select(Fleet).where(Fleet.id == fleet_id))
    fleet = r.scalar_one_or_none()
    if not fleet:
        raise HTTPException(status_code=404, detail="Fleet not found")
    is_owner = fleet.user_id == user.id or fleet.contact_email == user.email
    if not (is_owner or user.role == "admin"):
        raise HTTPException(status_code=403, detail="Not your fleet registration")
    return _to_response(fleet)
