import asyncio
from httpx import AsyncClient

async def test():
    # Login as mechanic to get token
    async with AsyncClient(base_url="http://127.0.0.1:8000/api") as client:
        res = await client.post("/auth/login", json={"email": "mechanic@demo.com", "password": "demo123456", "role": "mechanic"})
        token = res.json()["token"]
        
        headers = {"Authorization": f"Bearer {token}"}
        
        # Get incoming jobs
        res = await client.get("/jobs/incoming", headers=headers)
        jobs = res.json()["jobs"]
        print("Jobs:", jobs)
        
        if not jobs:
            print("No jobs found")
            return
            
        job_id = jobs[0]["id"]
        print(f"Trying to patch job {job_id}")
        
        res = await client.patch(f"/service/request/{job_id}/status", json={"status": "en_route"}, headers=headers)
        print("Patch result:", res.status_code, res.text)

asyncio.run(test())
