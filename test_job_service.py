import asyncio
import os
import sys

# Setup environment to load the app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))
os.environ["DATABASE_URL"] = "postgresql+asyncpg://clutchd:clutchd@localhost:5432/clutchd"
os.environ["SYNC_DATABASE_URL"] = "postgresql://clutchd:clutchd@localhost:5432/clutchd"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"

from app.db.session import AsyncSessionLocal
from app.services.job_service import patch_job_status
from app.services.job_service import get_job_for_user
from sqlalchemy import select
from app.models.user import User

async def run_test():
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(User).where(User.email == 'mechanic@demo.com'))
        user = r.scalar_one_or_none()
        if not user:
            print("No mechanic user")
            return
            
        print("Got user", user.id)
            
        from app.models.job import Job
        r = await db.execute(select(Job).where(Job.status == 'assigned'))
        job = r.scalar_one_or_none()
        
        if not job:
            r = await db.execute(select(Job))
            job = r.scalar_one_or_none()
            if not job:
                print("No jobs")
                return
            print("Job is", job.status)
            job.status = 'assigned'
            await db.flush()
            
        print("Got job", job.id)
            
        try:
            res = await patch_job_status(db, job, "en_route", None)
            print("Patch OK:", res)
        except Exception as e:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_test())
