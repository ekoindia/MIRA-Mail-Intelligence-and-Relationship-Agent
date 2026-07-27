"""
Single source of truth for "who does this report actually go to" — used by
the real send (both the scheduler and the manual "Send" button on the
Reports page), so every send path resolves recipients identically.

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

AO is still suppressed everywhere (see DISABLED_LEVELS below) — its sheet
column still holds the same placeholder testing address for every row, not
real per-unit addresses, so sending to it now would mail the same dummy
inbox repeatedly instead of the real AO officer. No report currently maps
AO in its org_levels, so this is presently a no-op safety net rather than
an active restriction. RBO was under the same restriction until real
per-unit addresses were added and verified (2026-07-27); remove DISABLED_
LEVELS entirely once AO gets real addresses too — no other code needs to
change, reports configured with AO org_levels will pick it back up
automatically.

CC policy (per the user):
  - LHO mail always CCs that circle's Circle Head Email.
  - Branch mail always CCs that branch's District Coordinator email.
  - Every mail, regardless of level, also CCs sbikiosk@eko.co.in.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy.orm import Session

from database.org_models import OrgLevel
from services.calling_sheet_service import load_calling_sheet, resolve_sheet_recipients
from services.org_service import resolve_org_recipients
from services.report_aggregation_service import filter_for_recipient

SHEET_BACKED_LEVELS = {OrgLevel.LHO, OrgLevel.BRANCH, OrgLevel.RBO, OrgLevel.AO}

# See module docstring — AO's sheet column is still a placeholder test
# address, not a real one, so it's excluded from every send until the
# sheet is updated. (RBO was removed from this set on 2026-07-27.)
DISABLED_LEVELS = {OrgLevel.AO}

# CC'd on every automated mail regardless of recipient level.
UNIVERSAL_CC = "sbikiosk@eko.co.in"


@dataclass
class RecipientRef:
    source: str  # "sheet" | "org"
    level: str
    name: str
    email: str
    unit_id: int | None = None  # only set for source == "org"
    cc_emails: str | None = None  # comma-separated, always includes UNIVERSAL_CC


def _merge_cc(*parts: str | None) -> str:
    """Combine CC sources into one de-duplicated, comma-separated list."""
    seen: dict[str, str] = {}  # casefolded -> original casing
    for part in parts:
        if not part:
            continue
        for addr in part.split(","):
            addr = addr.strip()
            if addr and addr.casefold() not in seen:
                seen[addr.casefold()] = addr
    return ", ".join(seen.values())


def resolve_recipients_for_levels(
    db: Session, levels: list[OrgLevel], calling_sheet_df: pd.DataFrame | None = None,
    allow_disabled_levels: bool = False,
) -> list[RecipientRef]:
    """
    De-duplicated (by email) recipient list across all given levels.

    allow_disabled_levels=True skips the RBO/AO suppression (DISABLED_LEVELS)
    for this call only — used for the Daily reports now that a handful of
    real RBO/AO addresses have been added to the sheet. resolve_sheet_recipients
    already only returns rows with a real, non-blank email in that column, so
    this naturally resolves to just the units that have actually been filled
    in (currently 2 RBOs + 2 AOs out of 10 RBOs / 33 AOs total) — every other
    still-blank unit is excluded automatically, no extra filtering needed.
    """
    levels = [level for level in levels if allow_disabled_levels or level not in DISABLED_LEVELS]

    out: list[RecipientRef] = []
    seen: set[str] = set()
    # email(lower) -> row count backing the current representative in `out`,
    # and its index — only populated for sheet-backed levels, where the
    # same real inbox can appear under more than one name label (confirmed
    # live: RBO "2" for one stray row, RBO "3" for the other 23). Keeping
    # whichever label has the most rows, rather than whichever the sheet
    # happens to list first, makes the displayed name deterministic and
    # representative instead of arbitrary.
    row_counts: dict[str, int] = {}
    index_by_email: dict[str, int] = {}

    needs_sheet = any(level in SHEET_BACKED_LEVELS for level in levels)
    df = calling_sheet_df
    if needs_sheet and df is None:
        df = load_calling_sheet()

    for level in levels:
        if level in SHEET_BACKED_LEVELS:
            for r in resolve_sheet_recipients(df, level.value):
                key = r["email"].lower().strip()
                if not key:
                    continue
                row_count = len(filter_for_recipient(df, level.value, r["name"], r["email"]))
                if key not in seen:
                    seen.add(key)
                    row_counts[key] = row_count
                    index_by_email[key] = len(out)
                    out.append(RecipientRef(
                        source="sheet", level=level.value, name=r["name"], email=r["email"],
                        cc_emails=_merge_cc(r.get("cc_email"), UNIVERSAL_CC),
                    ))
                elif row_count > row_counts[key]:
                    row_counts[key] = row_count
                    out[index_by_email[key]] = RecipientRef(
                        source="sheet", level=level.value, name=r["name"], email=r["email"],
                        cc_emails=_merge_cc(r.get("cc_email"), UNIVERSAL_CC),
                    )
        else:
            for r in resolve_org_recipients(db, level):
                key = r["email"].lower().strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append(RecipientRef(
                        source="org", level=level.value, name=r["name"], email=r["email"], unit_id=r["unit_id"],
                        cc_emails=_merge_cc(r.get("cc_emails"), UNIVERSAL_CC),
                    ))
    return out
