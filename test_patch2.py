import urllib.request
import json

def test():
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/auth/login", 
        data=json.dumps({"email": "mechanic@demo.com", "password": "demo123456", "role": "mechanic"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as f:
        res = json.loads(f.read().decode("utf-8"))
        token = res["token"]
        
    print("Logged in")
    
    req2 = urllib.request.Request(
        "http://127.0.0.1:8000/api/jobs/incoming",
        headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req2) as f:
        jobs = json.loads(f.read().decode("utf-8"))["jobs"]
        
    print("Jobs:", [j["id"] for j in jobs])
    if not jobs:
        return
        
    job_id = jobs[0]["id"]
    from urllib.error import HTTPError
    try:
        req3 = urllib.request.Request(
            f"http://127.0.0.1:8000/api/service/request/{job_id}/status",
            data=json.dumps({"status": "en_route"}).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="PATCH"
        )
        with urllib.request.urlopen(req3) as f:
            print("Patch success:", f.read().decode("utf-8"))
    except HTTPError as e:
        print("Patch error:", e.code, e.read().decode("utf-8"))

test()
