from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.adapters.odoo_xmlrpc import OdooXmlRpcGateway
from app.config import Settings
from app.domain.ports import ERPGateway

router = APIRouter()


@lru_cache
def get_erp_gateway() -> ERPGateway:
    return OdooXmlRpcGateway.from_settings(Settings())


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    odoo: Literal["ok", "unreachable"]


@router.get("/health")
def health(gateway: Annotated[ERPGateway, Depends(get_erp_gateway)]) -> HealthResponse:
    return HealthResponse(odoo="ok" if gateway.ping() else "unreachable")
