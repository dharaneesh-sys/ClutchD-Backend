from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession, get_current_user_optional
from app.models.marketplace import (
    MarketplaceCategory,
    MarketplaceOffer,
    MarketplaceOrder,
    MarketplaceOrderItem,
    MarketplaceProduct,
    MarketplaceProductReview,
)
from app.models.user import User
from app.schemas.marketplace import (
    CategoryListResponse,
    CategoryResponse,
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
