import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.enums import UserRole
from app.models.new_models import CustomerProfile
from app.models.user import User
from app.schemas.profile import ProfileUpdateRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])


async def _get_or_create_customer_profile(db: DbSession, user: User) -> CustomerProfile:
    """Return existing CustomerProfile or create one for a customer user."""
    r = await db.execute(select(CustomerProfile).where(CustomerProfile.user_id == user.id))
    profile = r.scalar_one_or_none()
    if profile is None:
        if user.role != UserRole.customer.value:
            raise HTTPException(status_code=404, detail="Profile not found")
        local = user.email.split("@")[0]
        profile = CustomerProfile(
            user_id=user.id,
            full_name=local.replace(".", " ").title(),
            phone="",
            address="",
        )
        db.add(profile)
        await db.flush()
    return profile


@router.get("/me")
async def profile_get_me(db: DbSession, user: CurrentUser):
    """Return consolidated profile for the current user based on their role."""
    base = {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
    }

    if user.role == UserRole.customer.value:
        profile = await _get_or_create_customer_profile(db, user)
        return {
            **base,
            "full_name": profile.full_name,
            "phone": profile.phone,
            "address": profile.address,
            "profile_photo_url": profile.profile_photo_url,
            "profile": {
                "id": str(profile.id),
                "full_name": profile.full_name,
                "phone": profile.phone,
                "address": profile.address,
                "profile_photo_url": profile.profile_photo_url,
                "created_at": profile.created_at.isoformat() if profile.created_at else None,
                "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
            },
        }

    if user.role == UserRole.mechanic.value:
        from app.models.mechanic import Mechanic

        r = await db.execute(select(Mechanic).where(Mechanic.user_id == user.id))
        m = r.scalar_one_or_none()
        if not m:
            raise HTTPException(status_code=404, detail="Mechanic profile not found")
        return {
            **base,
            "full_name": m.full_name,
            "phone": m.phone,
            "experience": m.experience,
            "expertise": list(m.expertise or []),
            "location": m.location_address,
            "rating": m.rating,
            "isOnline": m.available,
            "profile_photo_url": None,
            "kycStatus": m.kyc_status or ("verified" if m.verified else ("submitted" if (m.aadhaar_photo_url or m.license_photo_url) else "pending")),
            "kycNote": m.kyc_note,
            "aadhaarPhotoUrl": m.aadhaar_photo_url,
            "licensePhotoUrl": m.license_photo_url,
        }

    if user.role == UserRole.garage.value:
        from app.models.garage import Garage

        r = await db.execute(select(Garage).where(Garage.user_id == user.id))
        g = r.scalar_one_or_none()
        if not g:
            raise HTTPException(status_code=404, detail="Garage profile not found")
        return {
            **base,
            "full_name": g.garage_name,
            "owner_name": g.owner_name,
            "phone": g.phone,
            "location": g.location_address,
            "services": list(g.services or []),
            "mechanic_count": g.mechanic_count,
            "operating_hours": g.operating_hours,
            "rating": g.rating,
            "profile_photo_url": None,
            "kycStatus": g.kyc_status or ("verified" if g.verified else ("submitted" if (g.aadhaar_photo_url or g.license_photo_url) else "pending")),
            "kycNote": g.kyc_note,
            "aadhaarPhotoUrl": g.aadhaar_photo_url,
            "licensePhotoUrl": g.license_photo_url,
        }

    if user.role == UserRole.admin.value:
        return {
            **base,
            "full_name": user.email.split("@")[0].replace(".", " ").title(),
            "phone": "",
            "address": "",
            "profile_photo_url": None,
        }

    return base


@router.put("/me")
async def profile_update_me(body: ProfileUpdateRequest, db: DbSession, user: CurrentUser):
    """Update profile fields for the current user based on role."""
    if user.role == UserRole.customer.value:
        profile = await _get_or_create_customer_profile(db, user)
        if body.full_name is not None:
            profile.full_name = body.full_name
        if body.phone is not None:
            profile.phone = body.phone
        if body.address is not None:
            profile.address = body.address
        # Capture scalar values BEFORE flush: the model's onupdate=func.now()
        # makes accessing updated_at post-flush trigger a lazy refresh (IO),
        # which raises MissingGreenlet inside this sync context.
        resp = {
            "id": str(profile.id),
            "full_name": profile.full_name,
            "phone": profile.phone,
            "address": profile.address,
            "profile_photo_url": profile.profile_photo_url,
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        }
        await db.flush()
        return resp

    if user.role == UserRole.mechanic.value:
        from app.models.mechanic import Mechanic

        r = await db.execute(select(Mechanic).where(Mechanic.user_id == user.id))
        m = r.scalar_one_or_none()
        if not m:
            raise HTTPException(status_code=404, detail="Mechanic profile not found")
        if body.full_name is not None:
            m.full_name = body.full_name
        if body.phone is not None:
            m.phone = body.phone
        if body.address is not None:
            m.location_address = body.address
        if body.aadhaarPhotoUrl is not None:
            m.aadhaar_photo_url = body.aadhaarPhotoUrl
        if body.licensePhotoUrl is not None:
            m.license_photo_url = body.licensePhotoUrl
        if body.aadhaarPhotoUrl is not None or body.licensePhotoUrl is not None:
            if m.kyc_status in (None, "", "pending", "rejected"):
                m.kyc_status = "submitted"
                m.kyc_note = None
        await db.flush()
        return {"status": "updated"}

    if user.role == UserRole.garage.value:
        from app.models.garage import Garage

        r = await db.execute(select(Garage).where(Garage.user_id == user.id))
        g = r.scalar_one_or_none()
        if not g:
            raise HTTPException(status_code=404, detail="Garage profile not found")
        if body.full_name is not None:
            g.garage_name = body.full_name
        if body.phone is not None:
            g.phone = body.phone
        if body.address is not None:
            g.location_address = body.address
        if body.aadhaarPhotoUrl is not None:
            g.aadhaar_photo_url = body.aadhaarPhotoUrl
        if body.licensePhotoUrl is not None:
            g.license_photo_url = body.licensePhotoUrl
        if body.aadhaarPhotoUrl is not None or body.licensePhotoUrl is not None:
            if g.kyc_status in (None, "", "pending", "rejected"):
                g.kyc_status = "submitted"
                g.kyc_note = None
        await db.flush()
        return {"status": "updated"}

    raise HTTPException(status_code=403, detail="Profile updates not supported for this role")
