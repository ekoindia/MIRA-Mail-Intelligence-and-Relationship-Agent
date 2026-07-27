"""
Email-open tracking pixel endpoint.

Deliberately has NO auth dependency — it's fetched by the recipient's mail
client (Gmail/Outlook image loader), not by a logged-in frontend user, so
it can't require the app's session/JWT. The token itself (a random 16-byte
urlsafe string, generated in services/email_service.py at send time) is the
only "credential" here; it identifies the EmailLog row being tracked but
grants no read/write access to anything else.
"""
from __future__ import annotations

import base64
from datetime import datetime

from fastapi import APIRouter, Response

from database.db import get_db
from database.models import EmailLog

router = APIRouter(prefix="/api/track", tags=["tracking"])

# Smallest valid GIF: 1x1 transparent pixel.
_PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")


@router.get("/{token}.png")
def track_open(token: str):
    with get_db() as db:
        row = db.query(EmailLog).filter(EmailLog.tracking_token == token).first()
        if row is not None:
            if row.opened_at is None:
                row.opened_at = datetime.utcnow()
            row.open_count = (row.open_count or 0) + 1
    return Response(content=_PIXEL, media_type="image/gif", headers={"Cache-Control": "no-store"})
