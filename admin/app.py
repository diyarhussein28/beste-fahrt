"""Admin Panel — القسم 3.2 (phase 3): 'عرض حالة السائقين والعروض
والإحصاءات'. Read-only by design, same as the rest of the system — there is
no button here that books, cancels, or edits anything on the platform.
"""
from __future__ import annotations

import secrets as _secrets
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import HTMLResponse

from shared.config import get_secrets
from shared.db import list_drivers, list_open_jobs, list_recent_dispatches

from ops.kpis import compute_kpis

app = FastAPI(title="Fleet Dispatch Monitor — Admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
security = HTTPBasic()


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    expected_password = get_secrets().admin_secret_key
    correct_username = _secrets.compare_digest(credentials.username, "admin")
    correct_password = _secrets.compare_digest(credentials.password, expected_password)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="بيانات الدخول غير صحيحة",
            headers={"WWW-Authenticate": "Basic"},
        )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _=Depends(require_auth)) -> HTMLResponse:
    drivers = await list_drivers()
    jobs = await list_open_jobs()
    dispatches = await list_recent_dispatches()
    kpis = await compute_kpis(window_days=7)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "drivers": drivers,
            "jobs": jobs,
            "dispatches": dispatches,
            "kpis": kpis,
        },
    )
