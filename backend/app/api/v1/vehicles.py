from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.vehicle import Vehicle
from app.schemas.vehicle import VehicleCreate, VehicleResponse, VehicleUpdate

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.post("", response_model=VehicleResponse, status_code=status.HTTP_201_CREATED)
async def create_vehicle(vehicle_in: VehicleCreate, db: DbSession, current_user: CurrentUser):
    vehicle = Vehicle(
        user_id=current_user.id,
        **vehicle_in.model_dump(exclude_unset=True),
    )
    db.add(vehicle)
    await db.flush()
    await db.refresh(vehicle)
    return vehicle


@router.get("", response_model=list[VehicleResponse])
async def read_vehicles(db: DbSession, current_user: CurrentUser):
    result = await db.execute(select(Vehicle).where(Vehicle.user_id == current_user.id))
    vehicles = result.scalars().all()
    return vehicles


@router.get("/{vehicle_id}", response_model=VehicleResponse)
async def read_vehicle(vehicle_id: UUID, db: DbSession, current_user: CurrentUser):
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.user_id == current_user.id))
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle


@router.patch("/{vehicle_id}", response_model=VehicleResponse)
async def update_vehicle(vehicle_id: UUID, vehicle_in: VehicleUpdate, db: DbSession, current_user: CurrentUser):
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.user_id == current_user.id))
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    update_data = vehicle_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(vehicle, field, value)

    await db.flush()
    await db.refresh(vehicle)
    return vehicle


@router.delete("/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vehicle(vehicle_id: UUID, db: DbSession, current_user: CurrentUser):
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.user_id == current_user.id))
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    await db.delete(vehicle)
    await db.flush()
