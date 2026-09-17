from fastapi import APIRouter

from app.api.endpoints import (
    analytics,
    auth,
    company_participations,
    company_profile,
    credentials,
    dictionaries,
    export,
    health,
    integration_api,
    integrations,
    logs,
    manufacturers,
    notifications,
    relevance,
    sources,
    tenders,
    upper_software,
    registry_records,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(sources.router)
api_router.include_router(tenders.router)
api_router.include_router(integrations.router)
api_router.include_router(manufacturers.router)
api_router.include_router(dictionaries.router)
api_router.include_router(credentials.router)
api_router.include_router(company_profile.router)
api_router.include_router(company_profile.profiles_router)
api_router.include_router(company_participations.router)
api_router.include_router(analytics.router)
api_router.include_router(export.router)
api_router.include_router(logs.router)
api_router.include_router(notifications.router)
api_router.include_router(relevance.router)
api_router.include_router(integration_api.router)
api_router.include_router(upper_software.router)
api_router.include_router(registry_records.router)
