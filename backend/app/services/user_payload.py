from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.garage import Garage
from app.models.mechanic import Mechanic
from app.models.user import User


async def user_to_frontend_dict(db: AsyncSession, user: User) -> dict:
    base = {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
    }
    if user.role == "customer":
        local = user.email.split("@")[0]
        base["name"] = local.replace(".", " ").title()
        return base

    if user.role == "mechanic":
        r = await db.execute(select(Mechanic).where(Mechanic.user_id == user.id))
        m = r.scalar_one_or_none()
        if not m:
            base["name"] = base["email"].split("@")[0]
            return base
        return {
            **base,
            "name": m.full_name,
            "phone": m.phone,
            "experience": m.experience,
            "expertise": list(m.expertise or []),
            "location": m.location_address,
            "rating": m.rating,
            "isOnline": m.available,
        }

    if user.role == "garage":
        r = await db.execute(select(Garage).where(Garage.user_id == user.id))
        g = r.scalar_one_or_none()
        if not g:
            base["name"] = "Garage"
            return base
        return {
            **base,
            "name": g.garage_name,
            "ownerName": g.owner_name,
            "phone": g.phone,
            "location": g.location_address,
            "services": list(g.services or []),
            "mechanicCount": g.mechanic_count,
            "operatingHours": g.operating_hours,
            "rating": g.rating,
        }

    if user.role == "admin":
        base["name"] = "Admin"
        return base

    return base


async def load_user_with_profile(db: AsyncSession, user_id):
    r = await db.execute(
        select(User)
        .options(selectinload(User.mechanic_profile), selectinload(User.garage_profile))
        .where(User.id == user_id)
    )
    return r.scalar_one_or_none()
