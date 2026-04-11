import asyncio
import json
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._user_sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self._job_sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect_user(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._user_sockets[user_id].add(websocket)

    async def connect_job(self, job_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._job_sockets[job_id].add(websocket)

    def disconnect_user(self, user_id: str, websocket: WebSocket) -> None:
        if user_id in self._user_sockets:
            self._user_sockets[user_id].discard(websocket)
            if not self._user_sockets[user_id]:
                del self._user_sockets[user_id]

    def disconnect_job(self, job_id: str, websocket: WebSocket) -> None:
        if job_id in self._job_sockets:
            self._job_sockets[job_id].discard(websocket)
            if not self._job_sockets[job_id]:
                del self._job_sockets[job_id]

    async def send_json_to_user(self, user_id: str, message: dict[str, Any]) -> None:
        data = json.dumps(message)
        async with self._lock:
            sockets = list(self._user_sockets.get(user_id, ()))
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_user(user_id, ws)

    async def broadcast_job(self, job_id: str, message: dict[str, Any]) -> None:
        data = json.dumps(message)
        async with self._lock:
            sockets = list(self._job_sockets.get(job_id, ()))
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_job(job_id, ws)


manager = ConnectionManager()


async def push_status_update(
    customer_user_id: str,
    job_id: str | None,
    status: str,
    mechanic: dict | None = None,
) -> None:
    msg = {"type": "STATUS_UPDATE", "payload": {"status": status, "mechanic": mechanic}}
    await manager.send_json_to_user(customer_user_id, msg)
    if job_id:
        await manager.broadcast_job(job_id, msg)


async def push_location_update(customer_user_id: str, job_id: str | None, coords: list[float]) -> None:
    msg = {"type": "LOCATION_UPDATE", "payload": {"coords": coords}}
    await manager.send_json_to_user(customer_user_id, msg)
    if job_id:
        await manager.broadcast_job(job_id, msg)
