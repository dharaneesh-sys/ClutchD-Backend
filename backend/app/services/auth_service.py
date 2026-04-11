from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models.garage import Garage
from app.models.mechanic import Mechanic
from app.models.user import User
from app.schemas.auth import CustomerRegister, GarageRegister, MechanicRegister, SignupPayload
from app.services.user_payload import user_to_frontend_dict


class AuthError(Exception):
    def __init__(self, message: str, code: int = 400):
        self.message = message
        self.code = code


async def _ensure_email_free(db: AsyncSession, email: str) -> None:
    r = await db.execute(select(User).where(User.email == email.lower()))
    if r.scalar_one_or_none():
        raise AuthError("Email already registered", 409)


async def register_customer(db: AsyncSession, data: CustomerRegister) -> tuple[str, dict]:
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
    return token, payload


async def register_mechanic(db: AsyncSession, data: MechanicRegister) -> tuple[str, dict]:
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
    )
    db.add(mech)
    await db.flush()
    token = create_access_token(str(user.id), {"role": user.role})
    payload = await user_to_frontend_dict(db, user)
    return token, payload


async def register_garage(db: AsyncSession, data: GarageRegister) -> tuple[str, dict]:
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
    )
    db.add(g)
    await db.flush()
    token = create_access_token(str(user.id), {"role": user.role})
    payload = await user_to_frontend_dict(db, user)
    return token, payload


async def signup_from_payload(db: AsyncSession, body: SignupPayload) -> tuple[str, dict]:
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
        )
        return await register_mechanic(db, mr)
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
    )
    return await register_garage(db, gr)


async def login(db: AsyncSession, email: str, password: str) -> tuple[str, dict]:
    r = await db.execute(select(User).where(User.email == email.lower()))
    user = r.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        raise AuthError("Invalid email or password", 401)
    if not user.is_active:
        raise AuthError("Account disabled", 403)
    token = create_access_token(str(user.id), {"role": user.role})
    payload = await user_to_frontend_dict(db, user)
    return token, payload
