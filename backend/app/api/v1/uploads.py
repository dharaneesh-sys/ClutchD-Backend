import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.api.deps import CurrentUser
from app.core.config import get_settings
from app.core.limiter import limiter
from app.core.security import decode_token
from slowapi.util import get_remote_address

router = APIRouter(prefix="/uploads", tags=["uploads"])

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm"}
CHUNK_SIZE = 64 * 1024  # 64 KB chunks


def _upload_rate_limit_key(request: Request) -> str:
    """Rate-limit uploads per authenticated user, not per IP.

    Every device reaches the API through the Tailscale funnel, so the remote
    address is the funnel proxy for ALL users — a remote-address key makes the
    bucket global, and one active seller (or an old client retrying) starved
    everyone else with 429s that looked like 'photo upload failed'. Key by the
    JWT subject when a valid access token is present; fall back to the remote
    address otherwise.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        payload = decode_token(auth[7:].strip())
        if payload and payload.get("sub"):
            return f"user:{payload['sub']}"
    return get_remote_address(request)


@router.post("")
@limiter.limit("30/minute", key_func=_upload_rate_limit_key)
async def upload_file(request: Request, user: CurrentUser, file: UploadFile = File(...)):
    settings = get_settings()
    if not file.filename:
        raise HTTPException(status_code=422, detail="No filename")
    ext = Path(file.filename).suffix.lower()[:10] or ".bin"
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    max_b = settings.max_upload_mb * 1024 * 1024
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    path = upload_dir / name

    # Stream to disk in chunks instead of reading entire file into memory
    total_written = 0
    try:
        with open(path, "wb") as f:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > max_b:
                    # Clean up partial file and reject
                    f.close()
                    path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="File too large")
                f.write(chunk)
    except HTTPException:
        raise
    except Exception:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Upload failed")
    finally:
        await file.close()

    return {"url": f"/static/uploads/{name}", "filename": name}
