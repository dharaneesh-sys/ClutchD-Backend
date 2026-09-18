from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models.garage import Garage
from app.models.mechanic import Mechanic
from app.models.seller import Seller
from app.models.user import User
from app.schemas.auth import CustomerRegister, GarageRegister, MechanicRegister, SellerRegister, SignupPayload
from app.services.user_payload import user_to_frontend_dict


class AuthError(Exception):
    def __init__(self, message: str, code: int = 400):
        self.message = message
        self.code = code


async def _ensure_email_free(db: AsyncSession, email: str) -> None:
    r = await db.execute(select(User).where(User.email == email.lower()))
    if r.scalar_one_or_none():
        raise AuthError("Email already registered", 409)


async def register_customer(db: AsyncSession, data: CustomerRegister) -> tuple[str, dict, str]:
    await _ensure_email_free(db, data.email)
    user = User(
        email=data.email.lower(),
        password_hash=hash_password(data.password),
        role="customer",
    )
    db.add(user)
    await db.flush()
    token = create_access_token(str(user.id), {"role": user.role})
    payload = await user_to_frontend_dict(db, user)
    return token, payload, str(user.id)


async def register_mechanic(db: AsyncSession, data: MechanicRegister) -> tuple[str, dict, str]:
    settings = get_settings()
    verified = settings.dev_auto_verify_providers
    await _ensure_email_free(db, data.email)
    lat = data.latitude if data.latitude is not None else settings.default_map_lat
    lon = data.longitude if data.longitude is not None else settings.default_map_lon
    user = User(
        email=data.email.lower(),
        password_hash=hash_password(data.password),
        role="mechanic",
    )
    db.add(user)
    await db.flush()
    mech = Mechanic(
        user_id=user.id,
        full_name=data.fullName,
        phone=data.phone,
        experience=data.experience,
        expertise=data.expertise,
        location_address=data.location,
        lat=lat,
        lon=lon,
        verified=verified,
        available=True,
        aadhaar_photo_url=data.aadhaarPhotoUrl,
        license_photo_url=data.licensePhotoUrl,
        kyc_status="submitted" if (data.aadhaarPhotoUrl or data.licensePhotoUrl) else "pending",
    )
    db.add(mech)
    await db.flush()
    token = create_access_token(str(user.id), {"role": user.role})
    payload = await user_to_frontend_dict(db, user)
    return token, payload, str(user.id)


async def register_garage(db: AsyncSession, data: GarageRegister) -> tuple[str, dict, str]:
    settings = get_settings()
    verified = settings.dev_auto_verify_providers
    await _ensure_email_free(db, data.email)
    lat = data.latitude if data.latitude is not None else settings.default_map_lat
    lon = data.longitude if data.longitude is not None else settings.default_map_lon
    try:
        mc = int(data.mechanicCount)
    except ValueError:
        mc = 0
    user = User(
        email=data.email.lower(),
        password_hash=hash_password(data.password),
        role="garage",
    )
    db.add(user)
    await db.flush()
    g = Garage(
        user_id=user.id,
        garage_name=data.garageName,
        owner_name=data.ownerName,
        phone=data.phone,
        services=data.services,
        mechanic_count=mc,
        operating_hours=data.operatingHours,
        location_address=data.location,
        lat=lat,
        lon=lon,
        verified=verified,
        aadhaar_photo_url=data.aadhaarPhotoUrl,
        license_photo_url=data.licensePhotoUrl,
        kyc_status="submitted" if (data.aadhaarPhotoUrl or data.licensePhotoUrl) else "pending",
    )
    db.add(g)
    await db.flush()
    token = create_access_token(str(user.id), {"role": user.role})
    payload = await user_to_frontend_dict(db, user)
    return token, payload, str(user.id)


async def signup_from_payload(db: AsyncSession, body: SignupPayload) -> tuple[str, dict, str]:
    if body.role == "customer":
        cr = CustomerRegister(
            email=body.email or "",
            password=body.password or "",
            confirmPassword=body.confirmPassword,
        )
        return await register_customer(db, cr)
    if body.role == "mechanic":
        mr = MechanicRegister(
            email=body.email or "",
            password=body.password or "",
            confirmPassword=body.confirmPassword,
            fullName=body.fullName or "",
            phone=body.phone or "",
            experience=body.experience or "",
            expertise=body.expertise or [],
            location=body.location or "",
            latitude=body.latitude,
            longitude=body.longitude,
            aadhaarPhotoUrl=body.aadhaarPhotoUrl,
            licensePhotoUrl=body.licensePhotoUrl,
        )
        return await register_mechanic(db, mr)
    if body.role == "seller":
        sr = SellerRegister(
            email=body.email or "",
            password=body.password or "",
            confirmPassword=body.confirmPassword,
            storeName=body.storeName or "",
            ownerName=body.fullName or "",
            phone=body.phone or "",
            location=body.location or "",
        )
        return await register_seller(db, sr)
    gr = GarageRegister(
        email=body.email or "",
        password=body.password or "",
        confirmPassword=body.confirmPassword,
        garageName=body.garageName or "",
        ownerName=body.ownerName or "",
        phone=body.phone or "",
        location=body.location or "",
        services=body.services or [],
        mechanicCount=body.mechanicCount or "0",
        operatingHours=body.operatingHours or "",
        latitude=body.latitude,
        longitude=body.longitude,
        aadhaarPhotoUrl=body.aadhaarPhotoUrl,
        licensePhotoUrl=body.licensePhotoUrl,
    )
    return await register_garage(db, gr)


async def register_seller(db: AsyncSession, data: SellerRegister) -> tuple[str, dict, str]:
    await _ensure_email_free(db, data.email)
    user = User(
        email=data.email.lower(),
        password_hash=hash_password(data.password),
        role="seller",
    )
    db.add(user)
    await db.flush()
    s = Seller(
        user_id=user.id,
        store_name=data.storeName,
        owner_name=data.ownerName or "",
        phone=data.phone or "",
        location_address=data.location or "",
    )
    db.add(s)
    await db.flush()
    token = create_access_token(str(user.id), {"role": user.role})
    payload = await user_to_frontend_dict(db, user)
    return token, payload, str(user.id)


async def login(db: AsyncSession, email: str, password: str) -> tuple[str, dict, str]:
    r = await db.execute(select(User).where(User.email == email.lower()))
    user = r.scalar_one_or_none()
    if not user:
        # Unknown email → 404 (not 401) so the frontend can offer sign-up
        # instead of a dead-end "invalid credentials" error. Wrong password
        # on an existing account stays a generic 401 (no account enumeration).
        raise AuthError("No account found with this email. Please sign up first.", 404)
    if not verify_password(password, user.password_hash):
        raise AuthError("Invalid email or password", 401)
    if not user.is_active:
        raise AuthError("Account disabled", 403)
    token = create_access_token(str(user.id), {"role": user.role})
    payload = await user_to_frontend_dict(db, user)
    return token, payload, str(user.id)
