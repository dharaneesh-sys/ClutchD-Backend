from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession, get_current_user_optional
from app.models.marketplace import (
    MarketplaceCartItem,
    MarketplaceCategory,
    MarketplaceOffer,
    MarketplaceOrder,
    MarketplaceOrderItem,
    MarketplaceProduct,
    MarketplaceProductFitment,
    MarketplaceProductReview,
)
from app.models.user import User
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

    return CategoryListResponse(
        categories=[
            CategoryResponse(
                id=c.id,
                slug=c.slug,
                name=c.name,
                description=c.description,
                image=c.image,
                product_count=c.product_count,
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
    make: str | None = Query(None, max_length=100),
    model: str | None = Query(None, max_length=100),
    year: int | None = Query(None, ge=1900, le=2100),
    db: DbSession,
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
