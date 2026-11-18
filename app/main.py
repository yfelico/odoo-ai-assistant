from fastapi import FastAPI

from app.api.routes import router
from app.logging_config import configure_logging

configure_logging()

app = FastAPI(title="Odoo AI Assistant", version="0.1.0")
app.include_router(router)
