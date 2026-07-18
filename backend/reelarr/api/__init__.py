from fastapi import APIRouter

from reelarr.api import requests, settings, system, webhooks

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(settings.router)
api_router.include_router(requests.router)
api_router.include_router(system.router)
api_router.include_router(webhooks.router)
