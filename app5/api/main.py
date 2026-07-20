"""
FastAPI backend for the React frontend (web/). Thin REST layer over the
exact same database/services the Streamlit app (app.py) uses — no business
logic is duplicated here, only exposed. Both frontends can run against the
same database at once.

Run with:  uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.auth import get_current_user, login as auth_login
from api.routers import dashboard, gmail, logs, org_units, reports, schedules, sources, templates
from database.db import init_db
from services.scheduler_service import get_scheduler
from utils.logger import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    get_scheduler()  # same 1-minute pollers (manual + auto-distribution schedules) as the Streamlit app
    yield


app = FastAPI(title="Reporting Console API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginIn(BaseModel):
    username: str
    password: str


auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


@auth_router.post("/login")
def login(body: LoginIn):
    return auth_login(body.username, body.password)


@auth_router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return user


app.include_router(auth_router)
app.include_router(dashboard.router)
app.include_router(org_units.router)
app.include_router(reports.router)
app.include_router(templates.router)
app.include_router(sources.router)
app.include_router(schedules.router)
app.include_router(logs.router)
app.include_router(gmail.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
