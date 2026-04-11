import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.api.deps import CurrentUser
from app.core.config import get_settings

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("")
async def upload_file(user: CurrentUser, file: UploadFile = File(...)):
    settings = get_settings()
    if not file.filename:
        raise HTTPException(status_code=422, detail="No filename")
    ext = Path(file.filename).suffix.lower()[:10] or ".bin"
    if ext not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm"}:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    max_b = settings.max_upload_mb * 1024 * 1024
    data = await file.read()
    if len(data) > max_b:
        raise HTTPException(status_code=413, detail="File too large")
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    path = upload_dir / name
    path.write_bytes(data)
    return {"url": f"/static/uploads/{name}", "filename": name}
