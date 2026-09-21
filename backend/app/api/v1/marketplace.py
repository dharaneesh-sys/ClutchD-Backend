from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession, get_current_user_optional, require_roles
from app.models.marketplace import (
    MarketplaceCartItem,
    MarketplaceCategory,
    MarketplaceOffer,
    MarketplaceOrder,
    MarketplaceOrderItem,
    MarketplaceProduct,
    MarketplaceProductFitment,
    MarketplaceProductReview,
    MarketplaceVendor,
)
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.marketplace import (
    CartItemCreate,
    CartItemResponse,
    CartItemUpdate,
    CategoryListResponse,
    CategoryResponse,
    FitmentCheckResult,
    FitmentRecordResponse,
    OrderCreate,
    OrderItemData,
    OrderListResponse,
    OrderResponse,
    ProductListResponse,
    ProductResponse,
    ProductCreate,
    ProductUpdate,
    OfferValidateRequest,
    OfferValidateResponse,
    ProductReviewCreate,
    ProductReviewListResponse,
    ProductReviewResponse,
)

router = APIRouter(tags=["marketplace"])


# ── Categories ───────────────────────────────────────────────────────────

@router.get("/categories", response_model=CategoryListResponse)
async def list_categories(db: DbSession, search: str | None = Query(None, max_length=100)):
    query = select(MarketplaceCategory).order_by(MarketplaceCategory.name)

    if search:
        query = query.where(MarketplaceCategory.name.ilike(f"%{search}%"))

    result = await db.execute(query)
    categories = result.scalars().all()

    # Live per-category product counts — the product_count column is a stale
    # seed-time snapshot (bootstrap_db.py), so compute from marketplace_products.
    count_rows = await db.execute(
        select(MarketplaceProduct.category_id, func.count(MarketplaceProduct.id))
        .where(MarketplaceProduct.category_id.isnot(None))
        .group_by(MarketplaceProduct.category_id)
    )
    live_counts: dict[uuid.UUID, int] = {row[0]: int(row[1]) for row in count_rows.all()}

    return CategoryListResponse(
        categories=[
            CategoryResponse(
                id=c.id,
                slug=c.slug,
                name=c.name,
                description=c.description,
                image=c.image,
                product_count=live_counts.get(c.id, 0),
                created_at=c.created_at,
            )
            for c in categories
        ]
    )


# ── Products ─────────────────────────────────────────────────────────────

@router.get("/products/top-products", response_model=ProductListResponse)
async def top_products(
    db: DbSession,
    limit: int = Query(8, ge=1, le=50),
):
    """Return top-rated products for the homepage / marketplace landing."""
    query = (
        select(MarketplaceProduct)
        .order_by(MarketplaceProduct.rating.desc(), MarketplaceProduct.name.asc())
        .limit(limit)
    )
    result = await db.execute(query)
    products = result.scalars().all()

    return ProductListResponse(
        products=[
            ProductResponse(
                id=p.id,
                name=p.name,
                description=p.description,
                brand=p.brand,
                vendor_id=p.vendor_id,
                vendor=p.vendor,
                price=p.price,
                rating=p.rating,
                image=p.image,
                category_id=p.category_id,
                category=p.category,
                availability=p.availability,
                delivery_time=p.delivery_time,
                created_at=p.created_at,
            )
            for p in products
        ]
    )


@router.get("/products", response_model=ProductListResponse)
async def list_products(
    db: DbSession,
    category: str | None = Query(None, max_length=100),
    search: str | None = Query(None, max_length=200),
    min_price: Decimal | None = Query(None, ge=0),
    max_price: Decimal | None = Query(None, ge=0),
    brand: str | None = Query(None, max_length=100),
    in_stock: bool | None = Query(None),
    sort_by: str | None = Query(None, pattern=r"^(price-asc|price-desc|rating|popularity)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    query = select(MarketplaceProduct)

    if category:
        query = query.where(
            or_(
                MarketplaceProduct.category == category,
                MarketplaceProduct.category_id == category,
            )
        )
    if search:
        query = query.where(
            or_(
                MarketplaceProduct.name.ilike(f"%{search}%"),
                MarketplaceProduct.brand.ilike(f"%{search}%"),
                MarketplaceProduct.vendor.ilike(f"%{search}%"),
            )
        )
    if min_price is not None:
        query = query.where(MarketplaceProduct.price >= min_price)
    if max_price is not None:
        query = query.where(MarketplaceProduct.price <= max_price)
    if brand:
        query = query.where(MarketplaceProduct.brand.ilike(brand))
    if in_stock is not None:
        query = query.where(MarketplaceProduct.availability == in_stock)

    # Sorting
    if sort_by == "price-asc":
        query = query.order_by(MarketplaceProduct.price.asc())
    elif sort_by == "price-desc":
        query = query.order_by(MarketplaceProduct.price.desc())
    elif sort_by == "rating":
        query = query.order_by(MarketplaceProduct.rating.desc())
    elif sort_by == "popularity":
        query = query.order_by(MarketplaceProduct.rating.desc(), MarketplaceProduct.name.asc())
    else:
        query = query.order_by(MarketplaceProduct.name.asc())

    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    products = result.scalars().all()

    return ProductListResponse(
        products=[
            ProductResponse(
                id=p.id,
                name=p.name,
                description=p.description,
                brand=p.brand,
                vendor_id=p.vendor_id,
                vendor=p.vendor,
                price=p.price,
                rating=p.rating,
                image=p.image,
                category_id=p.category_id,
                category=p.category,
                availability=p.availability,
                delivery_time=p.delivery_time,
                created_at=p.created_at,
            )
            for p in products
        ]
    )


# ── Seller products ────────────────────────────────────────────────────


def to_product_response(p: MarketplaceProduct) -> ProductResponse:
    """Map a MarketplaceProduct row to the public ProductResponse schema."""
    return ProductResponse(
        id=p.id,
        name=p.name,
        description=p.description,
        brand=p.brand,
        vendor_id=p.vendor_id,
        vendor=p.vendor,
        price=p.price,
        rating=p.rating,
        image=p.image,
        category_id=p.category_id,
        category=p.category,
        availability=p.availability,
        delivery_time=p.delivery_time,
        created_at=p.created_at,
    )


async def _seller_display_name(db: DbSession, user: User) -> str:
    """Best-effort seller display name: garage/mechanic profile, else email prefix."""
    from app.models.garage import Garage
    from app.models.mechanic import Mechanic
    from app.models.seller import Seller

    result = await db.execute(select(Garage).where(Garage.user_id == user.id))
    garage = result.scalar_one_or_none()
    if garage and garage.garage_name:
        return garage.garage_name
    result = await db.execute(select(Seller).where(Seller.user_id == user.id))
    seller = result.scalar_one_or_none()
    if seller and seller.store_name:
        return seller.store_name
    result = await db.execute(select(Mechanic).where(Mechanic.user_id == user.id))
    mechanic = result.scalar_one_or_none()
    if mechanic and mechanic.full_name:
        return mechanic.full_name
    return (user.email or "seller").split("@")[0]


async def _get_or_create_vendor(
    db: DbSession, user: User, vendor_id: uuid.UUID | None
) -> MarketplaceVendor:
    """Return the explicit vendor, or find-or-create one from seller identity."""
    if vendor_id is not None:
        result = await db.execute(select(MarketplaceVendor).where(MarketplaceVendor.id == vendor_id))
        vendor = result.scalar_one_or_none()
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")
        return vendor
    name = await _seller_display_name(db, user)
    result = await db.execute(select(MarketplaceVendor).where(MarketplaceVendor.name == name))
    vendor = result.scalar_one_or_none()
    if vendor is None:
        vendor = MarketplaceVendor(name=name)
        db.add(vendor)
        await db.flush()
    return vendor


async def _resolve_category(
    db: DbSession, category: str | None, category_id: uuid.UUID | None
) -> tuple[uuid.UUID | None, str | None]:
    """Resolve category input to (category_id, category name)."""
    if category_id is not None:
        result = await db.execute(select(MarketplaceCategory).where(MarketplaceCategory.id == category_id))
        found = result.scalar_one_or_none()
        if not found:
            raise HTTPException(status_code=422, detail="Unknown category_id")
        return found.id, found.name
    if category is not None:
        raw = category.strip()
        if not raw:
            raise HTTPException(status_code=422, detail="Unknown category")
        try:
            parsed = uuid.UUID(raw)
        except ValueError:
            parsed = None
        if parsed is not None:
            result = await db.execute(select(MarketplaceCategory).where(MarketplaceCategory.id == parsed))
            found = result.scalar_one_or_none()
            if found:
                return found.id, found.name
        result = await db.execute(select(MarketplaceCategory).where(MarketplaceCategory.slug == raw))
        found = result.scalar_one_or_none()
        if found:
            return found.id, found.name
        result = await db.execute(select(MarketplaceCategory).where(MarketplaceCategory.name.ilike(raw)))
        found = result.scalar_one_or_none()
        if found:
            return found.id, found.name
        # The UI consolidates every non-accessory category into one "Spare
        # Parts" tile (slug "spare-parts") which the seeder never created as
        # a DB row — uploads filed under it used to 422. Ensure the row exists
        # so the product gets a real category_id and the tile count stays exact.
        if raw.lower().replace("_", "-") in {"spare-parts", "spareparts", "spares"}:
            existing = await db.execute(
                select(MarketplaceCategory).where(MarketplaceCategory.slug == "spare-parts")
            )
            spare = existing.scalar_one_or_none()
            if not spare:
                spare = MarketplaceCategory(
                    slug="spare-parts",
                    name="Spare Parts",
                    description="Engine, brake, electrical, suspension, filters and other replacement parts",
                    product_count=0,
                )
                db.add(spare)
                await db.flush()
            return spare.id, spare.name
        raise HTTPException(status_code=422, detail="Unknown category")
    return None, None


def _is_admin(user: User) -> bool:
    try:
        role = UserRole(user.role) if isinstance(user.role, str) else user.role
    except ValueError:
        return bool(user.is_superuser)
    return role == UserRole.admin or bool(user.is_superuser)


async def _ensure_owner_or_admin(db: DbSession, product: MarketplaceProduct, user: User) -> None:
    if _is_admin(user):
        return
    if product.seller_user_id is not None and product.seller_user_id == user.id:
        return
    if product.seller_user_id is None:
        display = await _seller_display_name(db, user)
        if product.vendor and product.vendor == display:
            return
    raise HTTPException(status_code=403, detail="Not your product")


_seller_roles = require_roles(UserRole.seller, UserRole.admin)


@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
@router.post("/marketplace/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(body: ProductCreate, db: DbSession, user: User = Depends(_seller_roles)):
    """Create a marketplace product as a parts seller (seller/admin roles).

    Images are uploaded first via POST /api/uploads; pass the returned
    ``url`` (``/static/uploads/...``) or an absolute http(s) URL as ``image``.
    """
    category_id, category_name = await _resolve_category(db, body.category, body.category_id)
    vendor = await _get_or_create_vendor(db, user, body.vendor_id)
    product = MarketplaceProduct(
        name=body.name,
        description=body.description,
        brand=body.brand,
        vendor_id=vendor.id,
        vendor=vendor.name,
        price=body.price,
        image=body.image,
        category_id=category_id,
        category=category_name,
        availability=body.availability,
        delivery_time=body.delivery_time,
        seller_user_id=user.id,
    )
    db.add(product)
    await db.flush()
    await db.refresh(product)
    return to_product_response(product)


@router.get("/products/my-listings", response_model=ProductListResponse)
@router.get("/marketplace/products/my-listings", response_model=ProductListResponse)
async def my_listings(db: DbSession, user: User = Depends(_seller_roles)):
    """List the calling seller's own products (legacy vendor-string rows included)."""
    display = await _seller_display_name(db, user)
    query = (
        select(MarketplaceProduct)
        .where(
            or_(
                MarketplaceProduct.seller_user_id == user.id,
                (MarketplaceProduct.seller_user_id.is_(None)) & (MarketplaceProduct.vendor == display),
            )
        )
        .order_by(MarketplaceProduct.created_at.desc())
    )
    result = await db.execute(query)
    products = result.scalars().all()
    return ProductListResponse(products=[to_product_response(p) for p in products])


@router.put("/products/{product_id}", response_model=ProductResponse)
@router.put("/marketplace/products/{product_id}", response_model=ProductResponse)
@router.patch("/products/{product_id}", response_model=ProductResponse)
@router.patch("/marketplace/products/{product_id}", response_model=ProductResponse)
async def update_product(product_id: uuid.UUID, body: ProductUpdate, db: DbSession, user: User = Depends(_seller_roles)):
    """Update own product; admins may update any product."""
    result = await db.execute(select(MarketplaceProduct).where(MarketplaceProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    await _ensure_owner_or_admin(db, product, user)
    if body.name is not None:
        product.name = body.name
    if body.price is not None:
        product.price = body.price
    if body.description is not None:
        product.description = body.description
    if body.brand is not None:
        product.brand = body.brand
    if body.image is not None:
        product.image = body.image
    if body.availability is not None:
        product.availability = body.availability
    if body.delivery_time is not None:
        product.delivery_time = body.delivery_time
    if body.vendor_id is not None:
        vendor = await _get_or_create_vendor(db, user, body.vendor_id)
        product.vendor_id = vendor.id
        product.vendor = vendor.name
    if body.category is not None or body.category_id is not None:
        category_id, category_name = await _resolve_category(db, body.category, body.category_id)
        product.category_id = category_id
        product.category = category_name
    await db.flush()
    await db.refresh(product)
    return to_product_response(product)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/marketplace/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(product_id: uuid.UUID, db: DbSession, user: User = Depends(_seller_roles)):
    """Delete own product; admins may delete any product."""
    result = await db.execute(select(MarketplaceProduct).where(MarketplaceProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    await _ensure_owner_or_admin(db, product, user)
    await db.delete(product)
    await db.flush()
    return None



@router.get("/products/{product_id}", response_model=ProductResponse)
async def get_product(product_id: uuid.UUID, db: DbSession):
    result = await db.execute(select(MarketplaceProduct).where(MarketplaceProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return ProductResponse(
        id=product.id,
        name=product.name,
        description=product.description,
        brand=product.brand,
        vendor_id=product.vendor_id,
        vendor=product.vendor,
        price=product.price,
        rating=product.rating,
        image=product.image,
        category_id=product.category_id,
        category=product.category,
        availability=product.availability,
        delivery_time=product.delivery_time,
        created_at=product.created_at,
    )


# ── Fitment ──────────────────────────────────────────────────────────────

@router.get("/marketplace/products/{product_id}/fitment", response_model=FitmentCheckResult)
async def check_product_fitment(
    product_id: uuid.UUID,
    db: DbSession,
    make: str | None = Query(None, max_length=100),
    model: str | None = Query(None, max_length=100),
    year: int | None = Query(None, ge=1900, le=2100),
):
    """Check if a product fits a given vehicle make/model/year.

    Queries the marketplace_product_fitments table for matching records.
    Accepts vehicle details directly as query params — no auth required.
    """
    if not make:
        return FitmentCheckResult(
            compatible=False,
            fitment_type="unknown",
            non_fitting_parts=["Select a vehicle to check compatibility."],
            source="api",
        )

    stmt = select(MarketplaceProductFitment).where(
        MarketplaceProductFitment.product_id == product_id,
        MarketplaceProductFitment.vehicle_make == make.lower(),
    )

    if model:
        stmt = stmt.where(
            MarketplaceProductFitment.vehicle_model.is_(None)
            | (MarketplaceProductFitment.vehicle_model == model.lower()),
        )

    results = await db.execute(stmt)
    fitments = results.scalars().all()

    if not fitments:
        return FitmentCheckResult(
            compatible=False,
            fitment_type="unknown",
            non_fitting_parts=[f"No fitment data available for {make} {model or ''}."],
            source="api",
        )

    # Best match: specific model > model-agnostic
    specific = [f for f in fitments if f.vehicle_model and f.vehicle_model == (model or "").lower()]
    generic = [f for f in fitments if not f.vehicle_model]
    best = specific[0] if specific else (generic[0] if generic else fitments[0])

    if year is not None:
        if best.year_start is not None and year < best.year_start:
            return FitmentCheckResult(
                compatible=False,
                fitment_type="incompatible",
                non_fitting_parts=[f"Compatible from {best.year_start} model year onwards."],
                source="api",
            )
        if best.year_end is not None and year > best.year_end:
            return FitmentCheckResult(
                compatible=False,
                fitment_type="incompatible",
                non_fitting_parts=[f"Compatible up to {best.year_end} model year."],
                source="api",
            )

    non_fitting = []
    if best.fitment_type == "requires_modification":
        non_fitting = [best.notes or "May require modifications for proper fitment."]

    return FitmentCheckResult(
        compatible=best.fitment_type != "incompatible",
        fitment_type=best.fitment_type,
        non_fitting_parts=non_fitting,
        source=best.source,
    )


@router.get("/marketplace/products/{product_id}/fitments", response_model=list[FitmentRecordResponse])
async def list_product_fitments(product_id: uuid.UUID, db: DbSession):
    """List all fitment records for a product (admin/catalog use)."""
    result = await db.execute(
        select(MarketplaceProductFitment)
        .where(MarketplaceProductFitment.product_id == product_id)
        .order_by(MarketplaceProductFitment.vehicle_make, MarketplaceProductFitment.vehicle_model)
    )
    return result.scalars().all()


# ── Product Reviews ──────────────────────────────────────────────────────

@router.get("/marketplace/products/{product_id}/reviews", response_model=ProductReviewListResponse)
async def list_product_reviews(product_id: uuid.UUID, db: DbSession):
    result = await db.execute(
        select(MarketplaceProductReview)
        .where(MarketplaceProductReview.product_id == product_id)
        .order_by(MarketplaceProductReview.created_at.desc())
    )
    reviews = result.scalars().all()

    return ProductReviewListResponse(
        reviews=[
            ProductReviewResponse(
                id=r.id,
                productId=r.product_id,
                userName=r.user_name,
                rating=r.rating,
                text=r.text,
                date=r.created_at,
                verified=r.verified,
            )
            for r in reviews
        ]
    )


@router.post("/marketplace/products/{product_id}/reviews", response_model=ProductReviewResponse, status_code=status.HTTP_201_CREATED)
async def create_product_review(
    product_id: uuid.UUID,
    body: ProductReviewCreate,
    db: DbSession,
    user: User | None = Depends(get_current_user_optional),
):
    review = MarketplaceProductReview(
        product_id=product_id,
        user_id=user.id if user else None,
        user_name=body.userName or (user.email.split("@")[0] if user else "Anonymous"),
        rating=body.rating,
        text=body.text,
        verified=bool(user),
    )
    db.add(review)
    await db.flush()
    await db.refresh(review)

    return ProductReviewResponse(
        id=review.id,
        productId=review.product_id,
        userName=review.user_name,
        rating=review.rating,
        text=review.text,
        date=review.created_at,
        verified=review.verified,
    )


# ── Offers / Coupons ─────────────────────────────────────────────────────

@router.post("/marketplace/offers/validate", response_model=OfferValidateResponse)
async def validate_offer(body: OfferValidateRequest, db: DbSession):
    result = await db.execute(
        select(MarketplaceOffer).where(
            MarketplaceOffer.code == body.code.upper().strip(),
            MarketplaceOffer.active.is_(True),
        )
    )
    offer = result.scalar_one_or_none()

    if not offer:
        return OfferValidateResponse(
            valid=False,
            code=body.code,
            discountAmount=Decimal("0"),
            message="Invalid or expired coupon code",
        )

    if body.purchaseAmount < offer.min_purchase:
        return OfferValidateResponse(
            valid=False,
            code=offer.code,
            discountAmount=Decimal("0"),
            message=f"Minimum purchase of ${offer.min_purchase:.0f} required",
        )

    if offer.discount_amount > 0:
        discount = min(offer.discount_amount, body.purchaseAmount)
    else:
        discount = (body.purchaseAmount * Decimal(offer.discount_percent)) / Decimal(100)

    return OfferValidateResponse(
        valid=True,
        code=offer.code,
        discountAmount=discount.quantize(Decimal("0.01")),
        message="Coupon applied successfully",
    )


# ── Orders ───────────────────────────────────────────────────────────────

@router.post("/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    body: OrderCreate,
    db: DbSession,
    user: User | None = Depends(get_current_user_optional),
):
    user_id = user.id if user else uuid.uuid4()

    total = sum(item.price * Decimal(item.quantity) for item in body.items)

    order = MarketplaceOrder(
        user_id=user_id,
        total=total.quantize(Decimal("0.01")),
        status="confirmed",
        address=body.address,
        payment=body.payment,
    )
    db.add(order)
    await db.flush()

    for item in body.items:
        order_item = MarketplaceOrderItem(
            order_id=order.id,
            product_id=item.product_id,
            name=item.name,
            quantity=item.quantity,
            price=item.price,
        )
        db.add(order_item)

    await db.flush()

    # Fetch created items
    items_result = await db.execute(
        select(MarketplaceOrderItem).where(MarketplaceOrderItem.order_id == order.id)
    )
    created_items = items_result.scalars().all()

    return OrderResponse(
        id=order.id,
        user_id=order.user_id,
        total=order.total,
        status=order.status,
        address=order.address,
        payment=order.payment,
        items=[
            OrderItemData(
                product_id=item.product_id,
                name=item.name,
                quantity=item.quantity,
                price=item.price,
            )
            for item in created_items
        ],
        created_at=order.created_at,
    )


@router.get("/orders", response_model=OrderListResponse)
async def list_orders(
    db: DbSession,
    user: User | None = Depends(get_current_user_optional),
    status_filter: str | None = Query(None, max_length=32),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    user_id = user.id if user else None
    query = select(MarketplaceOrder)

    if user_id:
        query = query.where(MarketplaceOrder.user_id == user_id)
    if status_filter:
        query = query.where(MarketplaceOrder.status == status_filter)

    query = query.order_by(MarketplaceOrder.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    orders = result.scalars().all()

    order_responses = []
    for order in orders:
        items_result = await db.execute(
            select(MarketplaceOrderItem).where(MarketplaceOrderItem.order_id == order.id)
        )
        order_items = items_result.scalars().all()

        order_responses.append(
            OrderResponse(
                id=order.id,
                user_id=order.user_id,
                total=order.total,
                status=order.status,
                address=order.address,
                payment=order.payment,
                items=[
                    OrderItemData(
                        product_id=item.product_id,
                        name=item.name,
                        quantity=item.quantity,
                        price=item.price,
                    )
                    for item in order_items
                ],
                created_at=order.created_at,
            )
        )

    return OrderListResponse(orders=order_responses)


# ── Seller sales ─────────────────────────────────────────────────

@router.get("/orders/sales", response_model=OrderListResponse)
@router.get("/marketplace/orders/sales", response_model=OrderListResponse)
async def list_my_sales(
    db: DbSession,
    user: User = Depends(_seller_roles),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Orders containing at least one product sold by the calling seller.

    An order belongs to a seller when one of its items references a product
    they own (seller_user_id, or the legacy vendor-name match used by
    /products/my-listings). The response only includes the matching items,
    so totals shown to the seller reflect their own goods.
    """
    display = await _seller_display_name(db, user)

    # Distinct orders joined to their items → products owned by this seller.
    result = await db.execute(
        select(MarketplaceOrder)
        .join(MarketplaceOrderItem, MarketplaceOrderItem.order_id == MarketplaceOrder.id)
        .join(
            MarketplaceProduct,
            MarketplaceProduct.id == MarketplaceOrderItem.product_id,
        )
        .where(
            or_(
                MarketplaceProduct.seller_user_id == user.id,
                (MarketplaceProduct.seller_user_id.is_(None)) & (MarketplaceProduct.vendor == display),
            )
        )
        .order_by(MarketplaceOrder.created_at.desc())
        .offset(offset)
        .limit(limit)
        .distinct()
    )
    orders = result.scalars().all()

    order_responses = []
    for order in orders:
        items_result = await db.execute(
            select(MarketplaceOrderItem).where(MarketplaceOrderItem.order_id == order.id)
        )
        all_items = items_result.scalars().all()
        # Keep only this seller's line items.
        product_ids = {i.product_id for i in all_items if i.product_id is not None}
        owned_ids = set()
        if product_ids:
            owned = await db.execute(
                select(MarketplaceProduct.id).where(
                    MarketplaceProduct.id.in_(product_ids),
                    or_(
                        MarketplaceProduct.seller_user_id == user.id,
                        (MarketplaceProduct.seller_user_id.is_(None)) & (MarketplaceProduct.vendor == display),
                    ),
                )
            )
            owned_ids = {row[0] for row in owned.all()}
        my_items = [i for i in all_items if i.product_id in owned_ids]
        if not my_items:
            continue
        order_responses.append(
            OrderResponse(
                id=order.id,
                user_id=order.user_id,
                total=sum((i.price * Decimal(i.quantity) for i in my_items), Decimal("0")).quantize(Decimal("0.01")),
                status=order.status,
                address=order.address,
                payment=order.payment,
                items=[
                    OrderItemData(
                        product_id=item.product_id,
                        name=item.name,
                        quantity=item.quantity,
                        price=item.price,
                    )
                    for item in my_items
                ],
                created_at=order.created_at,
            )
        )

    return OrderListResponse(orders=order_responses)


# ── Cart ─────────────────────────────────────────────────────────────────

@router.get("/marketplace/cart", response_model=list[CartItemResponse])
async def list_cart_items(
    db: DbSession,
    user: User | None = Depends(get_current_user_optional),
):
    """Get all cart items for the current user (or anonymous user)."""
    user_id = user.id if user else None
    query = select(MarketplaceCartItem).order_by(MarketplaceCartItem.created_at.desc())
    if user_id:
        query = query.where(MarketplaceCartItem.user_id == user_id)
    else:
        return []  # anonymous carts only via frontend local state

    result = await db.execute(query)
    items = result.scalars().all()
    return [
        CartItemResponse(
            id=item.id,
            user_id=item.user_id,
            product_id=item.product_id,
            vendor_id=item.vendor_id,
            quantity=item.quantity,
            created_at=item.created_at,
        )
        for item in items
    ]


@router.post("/marketplace/cart", response_model=CartItemResponse, status_code=status.HTTP_201_CREATED)
async def add_cart_item(
    body: CartItemCreate,
    db: DbSession,
    user: User | None = Depends(get_current_user_optional),
):
    """Add a product to the cart, or increment quantity if already present."""
    user_id = user.id if user else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Check for existing item
    existing = await db.execute(
        select(MarketplaceCartItem).where(
            MarketplaceCartItem.user_id == user_id,
            MarketplaceCartItem.product_id == body.product_id,
        )
    )
    existing_item = existing.scalar_one_or_none()

    if existing_item:
        existing_item.quantity += body.quantity
        await db.flush()
        await db.refresh(existing_item)
        item = existing_item
    else:
        item = MarketplaceCartItem(
            user_id=user_id,
            product_id=body.product_id,
            vendor_id=body.vendor_id,
            quantity=body.quantity,
        )
        db.add(item)
        await db.flush()
        await db.refresh(item)

    return CartItemResponse(
        id=item.id,
        user_id=item.user_id,
        product_id=item.product_id,
        vendor_id=item.vendor_id,
        quantity=item.quantity,
        created_at=item.created_at,
    )


@router.patch("/marketplace/cart/{item_id}", response_model=CartItemResponse)
async def update_cart_item(
    item_id: uuid.UUID,
    body: CartItemUpdate,
    db: DbSession,
    user: User | None = Depends(get_current_user_optional),
):
    """Update item quantity (set to 0 to remove)."""
    user_id = user.id if user else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    result = await db.execute(
        select(MarketplaceCartItem).where(
            MarketplaceCartItem.id == item_id,
            MarketplaceCartItem.user_id == user_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Cart item not found")

    if body.quantity == 0:
        await db.delete(item)
        await db.flush()
        raise HTTPException(status_code=204, detail="Item removed")

    item.quantity = body.quantity
    await db.flush()
    await db.refresh(item)

    return CartItemResponse(
        id=item.id,
        user_id=item.user_id,
        product_id=item.product_id,
        vendor_id=item.vendor_id,
        quantity=item.quantity,
        created_at=item.created_at,
    )


@router.delete("/marketplace/cart/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_cart_item(
    item_id: uuid.UUID,
    db: DbSession,
    user: User | None = Depends(get_current_user_optional),
):
    """Remove a single item from the cart."""
    user_id = user.id if user else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    result = await db.execute(
        select(MarketplaceCartItem).where(
            MarketplaceCartItem.id == item_id,
            MarketplaceCartItem.user_id == user_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Cart item not found")

    await db.delete(item)
    await db.flush()


@router.delete("/marketplace/cart", status_code=status.HTTP_204_NO_CONTENT)
async def clear_cart(
    db: DbSession,
    user: User | None = Depends(get_current_user_optional),
):
    """Remove all items from the user's cart."""
    user_id = user.id if user else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    result = await db.execute(
        select(MarketplaceCartItem).where(MarketplaceCartItem.user_id == user_id)
    )
    items = result.scalars().all()
    for item in items:
        await db.delete(item)
    await db.flush()
