#!/usr/bin/env python3
"""Create PostGIS extension, SQLAlchemy tables, and seed demo data."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_engine, get_session_local
from app.models.garage import Garage
from app.models.mechanic import Mechanic
from app.models.user import User


def _build_external_db_url() -> str | None:
    """If DATABASE_URL uses an internal Render hostname, construct the external .render.com URL.

    Render's ``fromDatabase`` connectionString uses an internal hostname such as
    ``dpg-xxxxx.internal`` which is unreachable on the free plan (no private
    networking).  This helper converts it to the public equivalent using the
    region suffix.

    Only rewrites hostnames starting with ``dpg-`` — third-party databases
    (Neon, etc.) are passed through unchanged.

    Returns the new URL (with ``+asyncpg`` driver) or ``None`` if no conversion
    is needed.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return None

    parsed = urlparse(url)
    host = parsed.hostname or ""

    if "render.com" in host or not host.startswith("dpg-"):
        return None

    dpg_id = host.split(".")[0]
    region = os.environ.get("RENDER_INSTANCE_REGION", "oregon")
    external_host = f"{dpg_id}.{region}-postgres.render.com"

    port = parsed.port or 5432
    if parsed.password:
        netloc = f"{parsed.username}:{parsed.password}@{external_host}:{port}"
    elif parsed.username:
        netloc = f"{parsed.username}@{external_host}:{port}"
    else:
        netloc = f"{external_host}:{port}"

    new_url = urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))

    if new_url.startswith("postgresql://"):
        new_url = new_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    return new_url


async def wait_for_database(max_attempts: int = 40, delay_sec: float = 2.0) -> bool:
    """Try to reach the database.  Returns True if reachable, False otherwise."""
    last_err: Exception | None = None
    from app.db.session import get_resolved_url
    print(f"DEBUG: DATABASE_URL={os.environ.get('DATABASE_URL', '(unset)')}")
    print(f"DEBUG: resolved_url={get_resolved_url()}")
    eng = get_engine()
    for attempt in range(1, max_attempts + 1):
        try:
            async with eng.connect() as conn:
                await conn.execute(text("SELECT 1"))
            print(f"Database reachable (attempt {attempt}).")
            return True
        except Exception as e:
            last_err = e
            print(f"Waiting for database... ({attempt}/{max_attempts}) {e!r}")
            await asyncio.sleep(delay_sec)

    external_url = _build_external_db_url()
    if external_url is None:
        print(f"Database not reachable after {max_attempts} attempts. Service will start without DB.")
        return False

    print("Internal hostname unreachable — retrying with external .render.com hostname...")
    new_engine = create_async_engine(
        external_url,
        pool_pre_ping=True,
    )
    try:
        async with new_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        print(f"External fallback also failed: {e}. Service will start without DB.")
        return False

    print("Database reachable via external hostname.")
    from app.db.session import reinit_engine
    reinit_engine(external_url)

    sync_url = external_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    os.environ["DATABASE_URL"] = external_url
    os.environ["SYNC_DATABASE_URL"] = sync_url
    from app.core.config import get_settings
    get_settings.cache_clear()
    return True


async def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config
    
    # Run alembic upgrade head
    alembic_cfg = Config("alembic.ini")
    # Set the DB URL from settings to ensure it matches environment
    from app.core.config import get_settings
    settings = get_settings()
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
    
    print("Running migrations...")
    # alembic.command is synchronous, but we are in an async loop.
    # For a simple bootstrap script, we can run it in a thread or just run it synchronously.
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
    print("Migrations complete.")


async def create_schema() -> None:
    import app.models  # noqa: F401 — register mappers
    eng = get_engine()

    async with eng.begin() as conn:
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        except Exception:
            logger.warning("PostGIS extension not available — spatial queries will use Python fallback")
        await conn.run_sync(Base.metadata.create_all)


async def get_or_create_user(session, email, password, role, is_superuser=False):
    from sqlalchemy.exc import IntegrityError
    r = await session.execute(select(User).where(User.email == email))
    user = r.scalar_one_or_none()
    if not user:
        try:
            async with session.begin_nested():
                user = User(
                    email=email,
                    password_hash=hash_password(password),
                    role=role,
                    is_superuser=is_superuser,
                )
                session.add(user)
        except IntegrityError:
            r = await session.execute(select(User).where(User.email == email))
            user = r.scalar_one_or_none()
    return user

async def seed() -> None:
    import app.models  # noqa: F401
    SessionLocal = get_session_local()

    async with SessionLocal() as session:
        # 1. Admin (Multiple variations to handle frontend validation and typos)
        await get_or_create_user(session, "admin21907.com", "clutchD123", "admin", True)
        await get_or_create_user(session, "admin@21907.com", "clutchD123", "admin", True)
        await get_or_create_user(session, "admin@1907.com", "clutchD123", "admin", True)

        # 2. Vijay Kumar
        mu1 = await get_or_create_user(session, "mechanic@demo.com", "demo123456", "mechanic")
        res = await session.execute(select(Mechanic).where(Mechanic.user_id == mu1.id))
        if not res.scalar_one_or_none():
            mech = Mechanic(
                user_id=mu1.id,
                full_name="Vijay Kumar",
                phone="9876543210",
                experience="5",
                expertise=["engine", "brakes", "electrical"],
                location_address="RS Puram, Coimbatore",
                lat=11.0168,
                lon=76.9558,
                rating=4.7,
                verified=True,
                available=True,
            )
            session.add(mech)

        # 3. SpeedFix Garage
        gu1 = await get_or_create_user(session, "garage@demo.com", "demo123456", "garage")
        res = await session.execute(select(Garage).where(Garage.user_id == gu1.id))
        if not res.scalar_one_or_none():
            garage = Garage(
                user_id=gu1.id,
                garage_name="SpeedFix Auto Garage",
                owner_name="Suresh Patel",
                phone="9876543211",
                services=["engine", "brakes", "ac", "electrical", "tires"],
                mechanic_count=8,
                operating_hours="8:00 AM - 9:00 PM",
                location_address="Saibaba Colony, Coimbatore",
                lat=11.025,
                lon=76.94,
                rating=4.5,
                verified=True,
            )
            session.add(garage)

        # ---- Customer ----
        await get_or_create_user(session, "customer@demo.com", "demo123456", "customer")

        await session.commit()
        print("Seeded all demo accounts:")
        print("  admin@21907.com / clutchD123")
        print("  mechanic@demo.com / demo123456")
        print("  garage@demo.com / demo123456")
        print("  customer@demo.com / demo123456")

        # ---- Marketplace seed ----
        await seed_marketplace(session)


async def seed_marketplace(session):
    from app.models.marketplace import (
        MarketplaceCategory,
        MarketplaceOffer,
        MarketplaceProduct,
        MarketplaceVendor,
    )
    from sqlalchemy.exc import IntegrityError

    # ── Categories ─────────────────────────────────────────────────────
    categories_data = [
        {"slug": "engine-parts", "name": "Engine Parts", "description": "Pistons, rings, gaskets, timing belts, and other engine components", "image": "/images/categories/engine.jpg", "product_count": 24},
        {"slug": "brake-parts", "name": "Brake Parts", "description": "Brake pads, discs, calipers, and brake fluid", "image": "/images/categories/brakes.jpg", "product_count": 18},
        {"slug": "electrical", "name": "Electrical Components", "description": "Spark plugs, batteries, alternators, and wiring harnesses", "image": "/images/categories/electrical.jpg", "product_count": 32},
        {"slug": "suspension", "name": "Suspension Parts", "description": "Shock absorbers, struts, springs, and bushings", "image": "/images/categories/suspension.jpg", "product_count": 15},
        {"slug": "filters", "name": "Filters", "description": "Oil filters, air filters, fuel filters, and cabin filters", "image": "/images/categories/filters.jpg", "product_count": 12},
        {"slug": "accessories", "name": "Accessories", "description": "Car care products, floor mats, covers, and interior accessories", "image": "/images/categories/accessories.jpg", "product_count": 35},
    ]

    category_map = {}
    for cd in categories_data:
        r = await session.execute(select(MarketplaceCategory).where(MarketplaceCategory.slug == cd["slug"]))
        cat = r.scalar_one_or_none()
        if not cat:
            cat = MarketplaceCategory(**cd)
            session.add(cat)
            await session.flush()
        category_map[cd["slug"]] = cat.id

    # ── Vendors ────────────────────────────────────────────────────────
    vendors_data = [
        {"name": "Auto Parts Co.", "description": "Leading supplier of automotive engine and suspension parts"},
        {"name": "Brake World", "description": "Specialists in braking systems and components"},
        {"name": "Spark Gears", "description": "Premium electrical and ignition components"},
        {"name": "Battery Hub", "description": "Wide range of automotive batteries"},
        {"name": "FilterPro", "description": "High-quality filtration solutions for all vehicles"},
    ]

    vendor_map = {}
    for vd in vendors_data:
        r = await session.execute(select(MarketplaceVendor).where(MarketplaceVendor.name == vd["name"]))
        vendor = r.scalar_one_or_none()
        if not vendor:
            vendor = MarketplaceVendor(**vd)
            session.add(vendor)
            await session.flush()
        vendor_map[vd["name"]] = vendor.id

    # ── Products ───────────────────────────────────────────────────────
    products_data = [
        {"name": "Engine Piston Ring Set", "description": "High-quality piston ring set for 4-cylinder engines", "brand": "Bosch", "vendor_name": "Auto Parts Co.", "price": 899, "rating": 4.5, "image": "/images/products/engine-piston.jpg", "category_slug": "engine-parts", "delivery_time": "2-3 days"},
        {"name": "Timing Belt Kit", "description": "Complete timing belt kit with tensioner for reliable timing", "brand": "MICO", "vendor_name": "Auto Parts Co.", "price": 1450, "rating": 4.7, "image": "/images/products/timing-belt.jpg", "category_slug": "engine-parts", "delivery_time": "2-3 days"},
        {"name": "Cylinder Head Gasket Set", "description": "Premium cylinder head gasket set for superior engine sealing", "brand": "TVS", "vendor_name": "Auto Parts Co.", "price": 2350, "rating": 4.3, "image": "/images/products/head-gasket.jpg", "category_slug": "engine-parts", "delivery_time": "3-5 days"},
        {"name": "Brake Pad Set (Ceramic)", "description": "Ceramic brake pads for quiet, low-dust stopping power", "brand": "Bosch", "vendor_name": "Brake World", "price": 1299, "rating": 4.8, "image": "/images/products/brake-pads.jpg", "category_slug": "brake-parts", "delivery_time": "1-2 days"},
        {"name": "Brake Disc Rotor (Vented)", "description": "Vented brake disc rotor for improved heat dissipation", "brand": "Valeo", "vendor_name": "Brake World", "price": 1899, "rating": 4.6, "image": "/images/products/brake-disc.jpg", "category_slug": "brake-parts", "delivery_time": "1-2 days"},
        {"name": "Brake Caliper Assembly", "description": "Complete brake caliper assembly with mounting hardware", "brand": "Denso", "vendor_name": "Brake World", "price": 3499, "rating": 4.4, "image": "/images/products/brake-caliper.jpg", "category_slug": "brake-parts", "delivery_time": "2-3 days"},
        {"name": "Spark Plug Iridium (Set of 4)", "description": "Iridium spark plugs for better ignition and fuel efficiency", "brand": "NGK", "vendor_name": "Spark Gears", "price": 599, "rating": 4.6, "image": "/images/products/spark-plugs.jpg", "category_slug": "electrical", "delivery_time": "1-2 days"},
        {"name": "Battery 12V 40Ah (Maintenance Free)", "description": "Maintenance-free 12V battery with long service life", "brand": "MICO", "vendor_name": "Battery Hub", "price": 3899, "rating": 4.7, "image": "/images/products/car-battery.jpg", "category_slug": "electrical", "delivery_time": "Same day"},
        {"name": "Alternator Assembly 90A", "description": "90-amp alternator assembly for reliable electrical charging", "brand": "Denso", "vendor_name": "Spark Gears", "price": 5299, "rating": 4.5, "image": "/images/products/alternator.jpg", "category_slug": "electrical", "delivery_time": "2-3 days"},
        {"name": "Shock Absorber Set (Front Pair)", "description": "Front pair shock absorbers for smooth ride control", "brand": "Bosch", "vendor_name": "Auto Parts Co.", "price": 2799, "rating": 4.3, "image": "/images/products/shock-absorber.jpg", "category_slug": "suspension", "delivery_time": "2-3 days"},
        {"name": "Coil Spring Kit (Rear)", "description": "Rear coil spring kit for improved load handling and stability", "brand": "TVS", "vendor_name": "Auto Parts Co.", "price": 3599, "rating": 4.4, "image": "/images/products/coil-spring.jpg", "category_slug": "suspension", "delivery_time": "3-5 days"},
        {"name": "Suspension Control Arm (Front Lower)", "description": "Front lower control arm with premium bushings installed", "brand": "Bosch", "vendor_name": "Auto Parts Co.", "price": 4299, "rating": 4.2, "image": "/images/products/control-arm.jpg", "category_slug": "suspension", "delivery_time": "2-3 days"},
        {"name": "Oil Filter F-101", "description": "High-flow oil filter with anti-drainback valve protection", "brand": "MICO", "vendor_name": "Auto Parts Co.", "price": 299, "rating": 4.5, "image": "/images/products/oil-filter.jpg", "category_slug": "filters", "delivery_time": "1-2 days"},
        {"name": "Air Filter Element (Panel Type)", "description": "Panel-type air filter for optimal engine airflow filtration", "brand": "Valeo", "vendor_name": "Auto Parts Co.", "price": 449, "rating": 4.3, "image": "/images/products/air-filter.jpg", "category_slug": "filters", "delivery_time": "2-3 days"},
        {"name": "Cabin Filter (Activated Carbon)", "description": "Activated carbon cabin filter for fresh interior air quality", "brand": "Valeo", "vendor_name": "Auto Parts Co.", "price": 549, "rating": 4.4, "image": "/images/products/cabin-filter.jpg", "category_slug": "filters", "delivery_time": "2-3 days"},
        {"name": "Car Floor Mat Set (TPE)", "description": "TPE floor mat set with anti-slip backing for all-weather use", "brand": "TVS", "vendor_name": "Auto Parts Co.", "price": 1199, "rating": 4.6, "image": "/images/products/floor-mats.jpg", "category_slug": "accessories", "delivery_time": "1-2 days"},
        {"name": "Seat Cover Set (Leatherette)", "description": "Leatherette seat cover set with universal fit for most cars", "brand": "TVS", "vendor_name": "Auto Parts Co.", "price": 2499, "rating": 4.5, "image": "/images/products/seat-covers.jpg", "category_slug": "accessories", "delivery_time": "2-3 days"},
        {"name": "Windshield Sun Shade (Foldable)", "description": "Foldable windshield sun shade protects interior from UV rays", "brand": "TVS", "vendor_name": "Auto Parts Co.", "price": 449, "rating": 4.2, "image": "/images/products/sun-shade.jpg", "category_slug": "accessories", "delivery_time": "1-2 days"},
    ]

    for pd in products_data:
        r = await session.execute(
            select(MarketplaceProduct).where(
                MarketplaceProduct.name == pd["name"],
                MarketplaceProduct.brand == pd["brand"],
            )
        )
        existing = r.scalar_one_or_none()
        if existing:
            continue
        product = MarketplaceProduct(
            name=pd["name"],
            description=pd["description"],
            brand=pd["brand"],
            vendor_id=vendor_map.get(pd["vendor_name"]),
            vendor=pd["vendor_name"],
            price=pd["price"],
            rating=pd["rating"],
            image=pd["image"],
            category_id=category_map.get(pd["category_slug"]),
            category=pd["category_slug"],
            availability=True,
            delivery_time=pd["delivery_time"],
        )
        session.add(product)

    # ── Offers ─────────────────────────────────────────────────────────
    offers_data = [
        {"code": "WELCOME15", "title": "New User Welcome", "description": "Get 15% off on your first purchase of auto parts. Minimum order of ₹500.", "discount_percent": 15, "min_purchase": 500},
        {"code": "FESTIVE10", "title": "Festive Season Special", "description": "Flat 10% discount on all engine parts and filters.", "discount_percent": 10, "min_purchase": 1000},
        {"code": "BRAKE20", "title": "Brake Service Bundle", "description": "Special 20% off on brake pads and disc rotors when bought together.", "discount_percent": 20, "min_purchase": 1500},
        {"code": "FREEDEL", "title": "Free Delivery Promo", "description": "Get ₹200 instant discount on orders above ₹2000.", "discount_amount": 200, "min_purchase": 2000},
        {"code": "FLASH25", "title": "Weekend Flash Sale", "description": "Extra 25% off on selected electrical components.", "discount_percent": 25, "min_purchase": 750},
    ]

    for od in offers_data:
        r = await session.execute(select(MarketplaceOffer).where(MarketplaceOffer.code == od["code"]))
        existing = r.scalar_one_or_none()
        if not existing:
            offer = MarketplaceOffer(
                code=od["code"],
                title=od["title"],
                description=od["description"],
                discount_percent=od.get("discount_percent", 0),
                discount_amount=od.get("discount_amount", 0),
                min_purchase=od.get("min_purchase", 0),
                active=True,
            )
            session.add(offer)

    # ── Product Reviews (sample) ────────────────────────────────────────
    from app.models.marketplace import MarketplaceProductReview

    # Get product IDs
    prod_result = await session.execute(select(MarketplaceProduct).limit(5))
    sample_products = prod_result.scalars().all()
    sample_reviews_data = [
        {"rating": 5, "text": "Excellent product! Fit perfectly on my car. Highly recommended.", "user_name": "Rajesh K"},
        {"rating": 4, "text": "Good quality for the price. Delivery was faster than expected.", "user_name": "Priya S"},
        {"rating": 5, "text": "Original branded product with great packaging. Will buy again.", "user_name": "Amit S"},
        {"rating": 4, "text": "Works as described. The fitment was exact and no issues.", "user_name": "Divya R"},
        {"rating": 3, "text": "Decent product but took longer to deliver than mentioned.", "user_name": "Vikram P"},
    ]

    for sp in sample_products:
        for rd in sample_reviews_data:
            r = await session.execute(
                select(MarketplaceProductReview).where(
                    MarketplaceProductReview.product_id == sp.id,
                    MarketplaceProductReview.user_name == rd["user_name"],
                )
            )
            existing = r.scalar_one_or_none()
            if not existing:
                review = MarketplaceProductReview(
                    product_id=sp.id,
                    user_name=rd["user_name"],
                    rating=rd["rating"],
                    text=rd["text"],
                    verified=True,
                )
                session.add(review)

    await session.commit()
    print(f"Seeded marketplace: {len(categories_data)} categories, {len(vendors_data)} vendors, {len(products_data)} products, {len(offers_data)} offers, {len(sample_products) * len(sample_reviews_data)} reviews")


async def main() -> None:
    db_ok = await wait_for_database()
    if db_ok:
        await create_schema()
        await run_migrations()
        await seed()
    else:
        print("WARNING: Database unavailable. Service will start without DB tables and seed data.")


if __name__ == "__main__":
    asyncio.run(main())
