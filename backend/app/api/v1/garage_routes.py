from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.enums import UserRole
from app.models.garage import Garage, GarageMechanic
from app.models.mechanic import Mechanic

router = APIRouter(prefix="/garage", tags=["garage"])


class AddMechanicBody(BaseModel):
    mechanic_id: UUID


@router.post("/add-mechanic")
async def add_mechanic(body: AddMechanicBody, db: DbSession, user: CurrentUser):
    mechanic_id = body.mechanic_id
    if user.role != UserRole.garage.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Garages only")
    gr = await db.execute(select(Garage).where(Garage.user_id == user.id))
    garage = gr.scalar_one_or_none()
    if not garage:
        raise HTTPException(status_code=404, detail="Garage profile not found")
    mr = await db.execute(select(Mechanic).where(Mechanic.id == mechanic_id))
    if not mr.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Mechanic not found")
    existing = await db.execute(
        select(GarageMechanic).where(
            GarageMechanic.garage_id == garage.id,
            GarageMechanic.mechanic_id == mechanic_id,
        )
    )
    if existing.scalar_one_or_none():
        return {"ok": True, "message": "Already linked"}
    db.add(GarageMechanic(garage_id=garage.id, mechanic_id=mechanic_id))
    await db.flush()
    return {"ok": True}


@router.get("/mechanics")
async def list_garage_mechanics(db: DbSession, user: CurrentUser):
    if user.role != UserRole.garage.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Garages only")
    gr = await db.execute(select(Garage).where(Garage.user_id == user.id))
    garage = gr.scalar_one_or_none()
    if not garage:
        raise HTTPException(status_code=404, detail="Garage profile not found")
    r = await db.execute(
        select(Mechanic)
        .join(GarageMechanic, GarageMechanic.mechanic_id == Mechanic.id)
        .where(GarageMechanic.garage_id == garage.id)
    )
    mechs = r.scalars().all()
    return {
        "mechanics": [
            {
                "id": str(m.id),
                "name": m.full_name,
                "phone": m.phone,
                "rating": m.rating,
                "verified": m.verified,
                "expertise": list(m.expertise or []),
            }
            for m in mechs
        ]
    }
