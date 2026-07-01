import logging
import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, func, delete
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, DbSession
from app.models.new_models import UserFavorite
from app.models.marketplace import MarketplaceProduct, MarketplaceCategory
from app.schemas.favorites import FavoriteCreate, FavoriteResponse, FavoriteListResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/favorites", tags=["favorites"])


@router.post("", response_model=FavoriteResponse)
async def favorite_add(body: FavoriteCreate, db: DbSession, user: CurrentUser):
    """Add a product to the user's favorites."""
    try:
        product_uuid = uuid.UUID(body.product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid product ID")

    # Verify product exists
    r = await db.execute(select(MarketplaceProduct).where(MarketplaceProduct.id == product_uuid))
    product = r.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Check if already favorited
    existing = await db.execute(
        select(UserFavorite).where(
            UserFavorite.user_id == user.id,
            UserFavorite.product_id == product_uuid,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Product already in favorites")

    cat_name = None
    if product and product.category_id:
        cr = await db.execute(
            select(MarketplaceCategory).where(MarketplaceCategory.id == product.category_id)
        )
        cat = cr.scalar_one_or_none()
        cat_name = cat.name if cat else None

    fav = UserFavorite(user_id=user.id, product_id=product_uuid)
    db.add(fav)
    await db.flush()

    return _favorite_to_response(fav, product, cat_name)


@router.get("", response_model=FavoriteListResponse)
async def favorite_list(db: DbSession, user: CurrentUser):
    """List all favorites for the current user with product details."""
    r = await db.execute(
        select(UserFavorite)
        .where(UserFavorite.user_id == user.id)
        .options(joinedload(UserFavorite.product))
        .order_by(UserFavorite.created_at.desc())
    )
    favorites = r.unique().scalars().all()

    items = []
    for fav in favorites:
        # Load category for each product
        cat_name = None
        if fav.product and fav.product.category_id:
            cr = await db.execute(
                select(MarketplaceCategory).where(MarketplaceCategory.id == fav.product.category_id)
            )
            cat = cr.scalar_one_or_none()
            cat_name = cat.name if cat else None

        items.append(_favorite_to_response(fav, fav.product, cat_name))

    return FavoriteListResponse(favorites=items, total=len(items))


@router.delete("/{product_id}")
async def favorite_remove(product_id: str, db: DbSession, user: CurrentUser):
    """Remove a product from the user's favorites."""
    try:
        product_uuid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid product ID")

    r = await db.execute(
        select(UserFavorite).where(
            UserFavorite.user_id == user.id,
            UserFavorite.product_id == product_uuid,
        )
    )
    fav = r.scalar_one_or_none()
    if not fav:
        raise HTTPException(status_code=404, detail="Favorite not found")

    await db.delete(fav)
    await db.flush()
    return {"message": "Removed from favorites"}


def _favorite_to_response(fav, product, category_name=None):
    return FavoriteResponse(
        id=str(fav.id),
        product_id=str(fav.product_id),
        created_at=fav.created_at.isoformat() if fav.created_at else None,
        product=FavoriteResponse.ProductInfo(
            id=str(product.id) if product else "",
            name=product.name if product else "Unknown",
            price=float(product.price) if product and product.price else 0.0,
            image_url=product.image if product and product.image else None,
            rating=float(product.rating) if product and product.rating else None,
            category_name=category_name or (product.category if product else None),
        ) if product else None,
    )
