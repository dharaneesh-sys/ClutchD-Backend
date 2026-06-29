from fastapi import APIRouter
from app.api.v1 import admin, auth, garage_routes, jobs, marketplace, matching_routes, notifications, payments, providers, reviews, service, uploads, vehicles

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(service.router)
api_router.include_router(providers.router)
api_router.include_router(jobs.router)
api_router.include_router(matching_routes.router)
api_router.include_router(garage_routes.router)
api_router.include_router(reviews.router)
api_router.include_router(payments.router)
api_router.include_router(admin.router)
api_router.include_router(uploads.router)
api_router.include_router(vehicles.router)
api_router.include_router(notifications.router)
api_router.include_router(marketplace.router)
