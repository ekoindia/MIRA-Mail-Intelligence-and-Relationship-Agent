"""
Single source of truth for "who does this report actually go to" — used by
both the real scheduled send (auto_distribution_service) and the Test Send
feature (api/routers/reports.py), so a test send always previews exactly
what the real send would do.

Two recipient sources, merged per report:
  - "sheet": LHO, Branch, RBO, and AO — derived directly from the Calling
    Sheet's own mail-ID columns (see
    calling_sheet_service.resolve_sheet_recipients). No manual
    configuration needed; adapts automatically as the sheet changes.
    ("RBO Email" and "AO Email ID" were added to the live sheet on
    2026-07-20 — both currently hold the same placeholder address for
    testing, per the user, and will be replaced with real per-unit
    addresses later.)
  - "org": Corporate Center — the sheet has no email column for this
    level, so it still comes from the manually-configured recipients on
    the Settings page (database.org_models.OrgUnit).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy.orm import Session

from database.org_models import OrgLevel
from services.calling_sheet_service import load_calling_sheet, resolve_sheet_recipients
from services.org_service import resolve_org_recipients

SHEET_BACKED_LEVELS = {OrgLevel.LHO, OrgLevel.BRANCH, OrgLevel.RBO, OrgLevel.AO}


@dataclass
class RecipientRef:
    source: str  # "sheet" | "org"
    level: str
    name: str
    email: str
    unit_id: int | None = None  # only set for source == "org"
    cc_emails: str | None = None  # comma-separated; only ever set for source == "org" today


def resolve_recipients_for_levels(
    db: Session, levels: list[OrgLevel], calling_sheet_df: pd.DataFrame | None = None,
) -> list[RecipientRef]:
    """De-duplicated (by email) recipient list across all given levels."""
    out: list[RecipientRef] = []
    seen: set[str] = set()

    needs_sheet = any(level in SHEET_BACKED_LEVELS for level in levels)
    df = calling_sheet_df
    if needs_sheet and df is None:
        df = load_calling_sheet()

    for level in levels:
        if level in SHEET_BACKED_LEVELS:
            for r in resolve_sheet_recipients(df, level.value):
                key = r["email"].lower().strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append(RecipientRef(source="sheet", level=level.value, name=r["name"], email=r["email"]))
        else:
            for r in resolve_org_recipients(db, level):
                key = r["email"].lower().strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append(RecipientRef(
                        source="org", level=level.value, name=r["name"], email=r["email"], unit_id=r["unit_id"],
                        cc_emails=r.get("cc_emails"),
                    ))
    return out


def resolve_recipient_by_ref(
    db: Session, source: str, level: str, *, unit_id: int | None = None, name: str | None = None,
) -> RecipientRef | None:
    """
    Re-resolve a single recipient by its (source, level, unit_id-or-name)
    reference — used by Test Send to look up one specific recipient without
    re-fetching/re-listing everyone.
    """
    if source == "org":
        if unit_id is None:
            return None
        from database.org_models import OrgUnit
        unit = db.query(OrgUnit).get(unit_id)
        if not unit:
            return None
        return RecipientRef(
            source="org", level=unit.level.value, name=unit.unit_name, email=unit.email, unit_id=unit.id,
            cc_emails=unit.cc_emails,
        )

    if source == "sheet":
        if not name:
            return None
        df = load_calling_sheet()
        for r in resolve_sheet_recipients(df, level):
            if r["name"].strip().casefold() == name.strip().casefold():
                return RecipientRef(source="sheet", level=level, name=r["name"], email=r["email"])
        return None

    return None
