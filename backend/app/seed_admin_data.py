#!/usr/bin/env python3
"""Seed comprehensive admin dashboard demo data — users, jobs, payments, disputes."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, text

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.mechanic import Mechanic
from app.models.garage import Garage
from app.models.job import Job
from app.models.payment import Payment
from app.models.dispute import Dispute
from app.models.notification import Notification


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


async def seed():
    import app.models  # noqa: F401

    async with AsyncSessionLocal() as session:
        # ── Existing seed data is not duplicated (get_or_create_user is idempotent) ──

        # ── 1. CUSTOMERS ───────────────────────────────────────────
        # customer@demo.com already exists from bootstrap
        cust_ananya = await get_or_create_user(session, "ananya.g@demo.com", "demo123456", "customer")
        cust_rohan = await get_or_create_user(session, "rohan.j@demo.com", "demo123456", "customer")
        cust_sneha = await get_or_create_user(session, "sneha.r@demo.com", "demo123456", "customer")
        cust_vikram = await get_or_create_user(session, "vikram.p@demo.com", "demo123456", "customer")
        cust_priya = await get_or_create_user(session, "priya.s@demo.com", "demo123456", "customer")
        cust_amit = await get_or_create_user(session, "amit.sharma@demo.com", "demo123456", "customer")
        cust_amit.is_active = False  # Suspended

        # Fetch existing users we need references to
        existing_users = {}
        for email in ["customer@demo.com", "mechanic@demo.com", "garage@demo.com",
                       "arjun@demo.com", "deepa@demo.com", "autocare@demo.com"]:
            r = await session.execute(select(User).where(User.email == email))
            u = r.scalar_one_or_none()
            if u:
                existing_users[email] = u

        cust_existing = existing_users.get("customer@demo.com")
        mech_vijay_user = existing_users.get("mechanic@demo.com")
        mech_arjun_user = existing_users.get("arjun@demo.com")
        mech_deepa_user = existing_users.get("deepa@demo.com")
        garage_speedfix_user = existing_users.get("garage@demo.com")
        garage_autocare_user = existing_users.get("autocare@demo.com")

        # Fetch existing profiles
        def fetch_profile(model, user_id):
            return session.execute(select(model).where(model.user_id == user_id))

        mech_vijay = (await fetch_profile(Mechanic, mech_vijay_user.id)).scalar_one_or_none() if mech_vijay_user else None
        mech_arjun = (await fetch_profile(Mechanic, mech_arjun_user.id)).scalar_one_or_none() if mech_arjun_user else None
        mech_deepa = (await fetch_profile(Mechanic, mech_deepa_user.id)).scalar_one_or_none() if mech_deepa_user else None
        garage_speedfix = (await fetch_profile(Garage, garage_speedfix_user.id)).scalar_one_or_none() if garage_speedfix_user else None
        garage_autocare = (await fetch_profile(Garage, garage_autocare_user.id)).scalar_one_or_none() if garage_autocare_user else None

        # ── 2. NEW VERIFIED MECHANICS ──────────────────────────────
        mech_rajesh_user = await get_or_create_user(session, "rajesh@demo.com", "demo123456", "mechanic")
        r = await session.execute(select(Mechanic).where(Mechanic.user_id == mech_rajesh_user.id))
        if not r.scalar_one_or_none():
            session.add(Mechanic(
                user_id=mech_rajesh_user.id, full_name="Rajesh K.",
                phone="9988776655", experience="6",
                expertise=["engine", "oil", "brakes", "suspension"],
                location_address="Singanallur, Coimbatore", lat=11.0035, lon=76.9710,
                rating=4.4, verified=True, available=True,
            ))

        mech_prakash_user = await get_or_create_user(session, "prakash@demo.com", "demo123456", "mechanic")
        r = await session.execute(select(Mechanic).where(Mechanic.user_id == mech_prakash_user.id))
        if not r.scalar_one_or_none():
            session.add(Mechanic(
                user_id=mech_prakash_user.id, full_name="Prakash M.",
                phone="8877665544", experience="4",
                expertise=["battery", "electrical", "tires", "diagnostics"],
                location_address="Podanur, Coimbatore", lat=10.9750, lon=76.9870,
                rating=4.3, verified=True, available=True,
            ))

        # ── 3. UNVERIFIED MECHANICS (KYC PENDING) ──────────────────
        mech_manoj_user = await get_or_create_user(session, "manoj@demo.com", "demo123456", "mechanic")
        r = await session.execute(select(Mechanic).where(Mechanic.user_id == mech_manoj_user.id))
        if not r.scalar_one_or_none():
            session.add(Mechanic(
                user_id=mech_manoj_user.id, full_name="Manoj S.",
                phone="7766554433", experience="2",
                expertise=["oil", "battery", "tires"],
                location_address="Saravanampatti, Coimbatore", lat=11.0780, lon=76.9940,
                rating=4.0, verified=False, available=True,
            ))

        mech_sunita_user = await get_or_create_user(session, "sunita@demo.com", "demo123456", "mechanic")
        r = await session.execute(select(Mechanic).where(Mechanic.user_id == mech_sunita_user.id))
        if not r.scalar_one_or_none():
            session.add(Mechanic(
                user_id=mech_sunita_user.id, full_name="Sunita Verma",
                phone="6655443322", experience="7",
                expertise=["engine", "transmission", "ac", "electrical"],
                location_address="Kovaipudur, Coimbatore", lat=10.9890, lon=76.9320,
                rating=4.8, verified=False, available=True,
            ))

        # ── 4. UNVERIFIED GARAGES (KYC PENDING) ────────────────────
        garage_citymotors_user = await get_or_create_user(session, "citymotors@demo.com", "demo123456", "garage")
        r = await session.execute(select(Garage).where(Garage.user_id == garage_citymotors_user.id))
        if not r.scalar_one_or_none():
            session.add(Garage(
                user_id=garage_citymotors_user.id, garage_name="City Motors",
                owner_name="Rajesh Kumar", phone="9988112233",
                services=["engine", "transmission", "brakes", "tires", "ac"],
                mechanic_count=5, operating_hours="9:00 AM - 8:00 PM",
                location_address="Town Hall, Coimbatore", lat=11.0110, lon=76.9620,
                rating=4.2, verified=False,
            ))

        garage_precision_user = await get_or_create_user(session, "precision@demo.com", "demo123456", "garage")
        r = await session.execute(select(Garage).where(Garage.user_id == garage_precision_user.id))
        if not r.scalar_one_or_none():
            session.add(Garage(
                user_id=garage_precision_user.id, garage_name="Precision Garage",
                owner_name="Anand M.", phone="8877001122",
                services=["electrical", "diagnostics", "suspension", "oil", "battery"],
                mechanic_count=3, operating_hours="8:00 AM - 9:00 PM",
                location_address="Ramanathapuram, Coimbatore", lat=10.9950, lon=76.9430,
                rating=4.1, verified=False,
            ))

        await session.flush()  # Ensure all user/profile IDs are assigned

        # Re-fetch mechanic/garage profiles to get their IDs for jobs
        def get_profile(model, user_id):
            return session.execute(select(model).where(model.user_id == user_id))

        mech_vijay = (await get_profile(Mechanic, mech_vijay_user.id)).scalar_one_or_none()
        mech_arjun = (await get_profile(Mechanic, mech_arjun_user.id)).scalar_one_or_none()
        mech_deepa = (await get_profile(Mechanic, mech_deepa_user.id)).scalar_one_or_none()
        mech_rajesh = (await get_profile(Mechanic, mech_rajesh_user.id)).scalar_one_or_none()
        mech_prakash = (await get_profile(Mechanic, mech_prakash_user.id)).scalar_one_or_none()
        garage_speedfix = (await get_profile(Garage, garage_speedfix_user.id)).scalar_one_or_none()
        garage_autocare = (await get_profile(Garage, garage_autocare_user.id)).scalar_one_or_none()

        now = datetime.now(timezone.utc)

        # ── 5. JOBS ────────────────────────────────────────────────
        # Helper
        def make_job(customer, issue_tag, description, status, lat, lon,
                     assigned_mech=None, assigned_garage=None,
                     total_amount=None, price=None, created_offset=None,
                     request_type="auto"):
            return Job(
                user_id=customer.id,
                issue_tag=issue_tag,
                description=description,
                request_type=request_type,
                status=status,
                customer_lat=lat,
                customer_lon=lon,
                assigned_mechanic_id=assigned_mech.id if assigned_mech else None,
                assigned_garage_id=assigned_garage.id if assigned_garage else None,
                assigned_type="mechanic" if assigned_mech else ("garage" if assigned_garage else None),
                total_amount=total_amount,
                price=price,
                created_at=now - (created_offset or timedelta(minutes=5)),
            )

        jobs_data = []
        coords = [
            (11.0188, 76.9758), (11.0240, 76.9620), (11.0080, 76.9450),
            (11.0150, 76.9680), (11.0300, 76.9550), (11.0050, 76.9810),
            (11.0120, 76.9510), (11.0200, 76.9730), (11.0260, 76.9600),
            (11.0100, 76.9380), (11.0170, 76.9690), (11.0220, 76.9460),
        ]
        # Searching jobs (Force Assign button visible)
        jobs_data.append(make_job(cust_ananya, "flat_tire", "Tyre punctured on the highway, need spare fitting.",
                                   "searching", *coords[0], created_offset=timedelta(minutes=5), price=1200))
        jobs_data.append(make_job(cust_rohan, "battery_dead", "Car won't start, battery completely dead. Need jump start.",
                                   "searching", *coords[1], created_offset=timedelta(minutes=2), price=800))

        # En Route jobs
        jobs_data.append(make_job(cust_sneha, "engine_failure", "Engine stalling at signals, check engine light blinking.",
                                   "en_route", *coords[2], assigned_mech=mech_vijay,
                                   created_offset=timedelta(minutes=12), price=2500))
        jobs_data.append(make_job(cust_vikram, "brake_issue", "Brakes making grinding noise, pedal feels soft.",
                                   "en_route", *coords[3], assigned_mech=mech_arjun,
                                   created_offset=timedelta(minutes=18), price=1800))

        # In Progress jobs
        jobs_data.append(make_job(cust_priya, "ac_not_working", "AC blowing warm air, compressor not engaging.",
                                   "in_progress", *coords[4], assigned_garage=garage_speedfix,
                                   created_offset=timedelta(minutes=30), price=3500))
        jobs_data.append(make_job(cust_ananya, "electrical", "Power windows not working, dashboard lights flickering.",
                                   "in_progress", *coords[5], assigned_mech=mech_deepa,
                                   created_offset=timedelta(minutes=45), price=2200))
        jobs_data.append(make_job(cust_rohan, "oil_leak", "Oil leaking underneath, low oil pressure warning on.",
                                   "in_progress", *coords[6], assigned_mech=mech_rajesh,
                                   created_offset=timedelta(minutes=60), price=1000))

        # Nearing Completion (in_progress with total_amount set = nearing completion display)
        jobs_data.append(make_job(cust_sneha, "transmission", "Gear slipping between 2nd and 3rd, transmission fluid dark.",
                                   "in_progress", *coords[7], assigned_garage=garage_autocare,
                                   total_amount=8500, created_offset=timedelta(hours=2)))

        # Completed jobs (with payments below)
        jobs_data.append(make_job(cust_vikram, "flat_tire", "Nail puncture in rear tire, patched and balanced.",
                                   "completed", *coords[8], assigned_mech=mech_prakash,
                                   total_amount=1200, created_offset=timedelta(hours=5)))
        jobs_data.append(make_job(cust_priya, "battery_dead", "Battery replacement, new Amaron battery installed.",
                                   "completed", *coords[9], assigned_mech=mech_vijay,
                                   total_amount=800, created_offset=timedelta(hours=8)))
        jobs_data.append(make_job(cust_existing, "overheating", "Coolant leak fixed, radiator hose replaced, system flushed.",
                                   "completed", *coords[10], assigned_garage=garage_speedfix,
                                   total_amount=4500, created_offset=timedelta(days=1)))

        # Cancelled job
        jobs_data.append(make_job(cust_rohan, "oil_leak", "Scheduled oil change — customer cancelled.",
                                   "cancelled", *coords[11], assigned_mech=mech_arjun,
                                   created_offset=timedelta(hours=3)))

        for job in jobs_data:
            existing = await session.execute(select(Job).where(
                Job.user_id == job.user_id,
                Job.issue_tag == job.issue_tag,
                Job.created_at == job.created_at,
                Job.status == job.status,
            ))
            if not existing.scalar_one_or_none():
                session.add(job)

        await session.flush()

        # Re-fetch jobs to get their IDs
        all_jobs = {}
        for j in jobs_data:
            r = await session.execute(select(Job).where(
                Job.user_id == j.user_id,
                Job.issue_tag == j.issue_tag,
                Job.created_at == j.created_at,
                Job.status == j.status,
            ))
            job = r.scalar_one_or_none()
            if job:
                all_jobs[j.issue_tag + "_" + j.status] = job

        # ── 6. PAYMENTS (for completed jobs) ───────────────────────
        completed_jobs = [j for j in jobs_data if j.status == "completed"]
        payment_amounts = [120000, 80000, 450000]  # in paise
        for idx, j in enumerate(completed_jobs):
            r = await session.execute(select(Job).where(Job.id == all_jobs.get(j.issue_tag + "_completed", uuid.uuid4()).id if all_jobs.get(j.issue_tag + "_completed") else None))
            job_obj = all_jobs.get(j.issue_tag + "_completed")
            if job_obj:
                existing_p = await session.execute(
                    select(Payment).where(Payment.job_id == job_obj.id, Payment.status == "captured")
                )
                if not existing_p.scalar_one_or_none():
                    session.add(Payment(
                        job_id=job_obj.id,
                        user_id=job_obj.user_id,
                        amount=payment_amounts[idx],
                        currency="inr",
                        provider="manual",
                        status="captured",
                        method="upi",
                        created_at=now - timedelta(hours=5) + timedelta(hours=idx * 3),
                    ))

        # ── 7. DISPUTES ────────────────────────────────────────────
        dispute_defs = [
            {"job_tag": "flat_tire_completed", "customer": cust_vikram, "notes": "The mechanic quoted ₹800 in chat but charged ₹1200 after completing the work. No prior approval for the extra amount.",
             "status": "open", "created_offset": timedelta(hours=4)},
            {"job_tag": "engine_failure_en_route", "customer": cust_sneha, "notes": "Waited for 2 hours after booking. Mechanic never showed up and stopped answering calls. Wasted my entire morning.",
             "status": "open", "created_offset": timedelta(hours=1)},
            {"job_tag": "oil_leak_in_progress", "customer": cust_rohan, "notes": "After oil change, engine light is still on. The mechanic says it's unrelated but the problem started right after the service.",
             "status": "open", "created_offset": timedelta(minutes=45)},
            {"job_tag": "overheating_completed", "customer": cust_existing, "notes": "The garage scratched and dented my car hood while working on it. They denied responsibility. Need compensation for bodywork.",
             "status": "open", "created_offset": timedelta(hours=20)},
            {"job_tag": "oil_leak_cancelled", "customer": cust_rohan, "notes": "I cancelled within 5 minutes of booking but was still charged a ₹500 cancellation fee. This is unfair as no mechanic had even started heading my way.",
             "status": "resolved", "created_offset": timedelta(hours=2)},
            {"job_tag": "brake_issue_en_route", "customer": cust_vikram, "notes": "Mechanic arrived 1 hour late despite being only 2 km away. I had to reschedule my meeting. Request compensation for the delay.",
             "status": "investigating", "created_offset": timedelta(minutes=30)},
        ]

        for dd in dispute_defs:
            job_obj = all_jobs.get(dd["job_tag"])
            if not job_obj:
                continue
            existing_d = await session.execute(
                select(Dispute).where(Dispute.job_id == job_obj.id, Dispute.notes == dd["notes"])
            )
            if not existing_d.scalar_one_or_none():
                resolution = None
                resolved_at = None
                if dd["status"] == "resolved":
                    resolution = "Investigated and determined the cancellation was automated. Issued a full refund of ₹500 as a goodwill gesture."
                    resolved_at = now - timedelta(hours=1)
                session.add(Dispute(
                    job_id=job_obj.id,
                    status=dd["status"],
                    notes=dd["notes"],
                    resolution=resolution,
                    created_at=now - dd["created_offset"],
                    resolved_at=resolved_at,
                ))

        await session.commit()

        print("✅ Admin seed data complete!")
        print()
        print("Jobs created:")
        status_counts = {}
        for j in jobs_data:
            status_counts[j.status] = status_counts.get(j.status, 0) + 1
        for s, c in status_counts.items():
            print(f"  {s}: {c}")
        print(f"  Total: {len(jobs_data)}")
        print()


if __name__ == "__main__":
    asyncio.run(seed())
