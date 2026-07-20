"""
One-time (idempotent) seed script: creates/updates a ReportSource pointing
at the real Calling Sheet ("Calling Sheet New" tab) for every report type
that has clean, unambiguous matching columns in that sheet — see
services/report_aggregation_service.AGGREGATORS for the exact list and
services/calling_sheet_service.py for the column mapping.

Safe to re-run: matches existing rows by name and updates them instead of
duplicating. Run with:

    python wire_calling_sheet_sources.py

After running, each of these reports' auto-distribution schedule can be
turned on from the Scheduler page — fetch step re-reads the live sheet,
send step calls services/segmented_distribution_service.apply_segmented_overrides
to build each recipient's own filtered attachment + computed metrics.

Reports NOT wired here (no source created) because the sheet has no clean
matching data for them yet:
  - Re-KYC & Inoperative Accounts (Daily/Weekly) -> free-text promises only
  - Server Issue (Weekly)                        -> no matching columns
  - CSP Physical Camp (Weekly)                    -> no matching columns
  - Inputs for Month-on-Month Growth (Monthly)    -> no matching columns
"""
from __future__ import annotations

from database.db import get_db, init_db
from database.models import ReportMaster, User
from database.report_source_models import ReportSource, SourceType
from services.calling_sheet_service import DEFAULT_SHEET_ID, DEFAULT_TAB
from services.report_aggregation_service import AGGREGATORS


def run() -> None:
    init_db()
    with get_db() as db:
        admin = db.query(User).filter(User.role == "Admin").first()
        created = 0
        updated = 0
        skipped = []

        for report_name in AGGREGATORS:
            report_master = db.query(ReportMaster).filter(ReportMaster.report_name == report_name).first()
            if not report_master:
                skipped.append(report_name)
                continue

            source_name = f"Calling Sheet — {report_name}"
            existing = db.query(ReportSource).filter(ReportSource.name == source_name).first()
            if existing:
                existing.source_type = SourceType.GOOGLE_SHEET
                existing.google_sheet_id = DEFAULT_SHEET_ID
                existing.google_sheet_tab = DEFAULT_TAB
                existing.report_master_id = report_master.id
                existing.is_active = True
                updated += 1
                print(f"  updated source: {source_name}")
            else:
                db.add(ReportSource(
                    name=source_name,
                    report_master_id=report_master.id,
                    source_type=SourceType.GOOGLE_SHEET,
                    google_sheet_id=DEFAULT_SHEET_ID,
                    google_sheet_tab=DEFAULT_TAB,
                    filename_template="{name}_{date}.xlsx",
                    is_active=True,
                    created_by=admin.id if admin else None,
                ))
                created += 1
                print(f"  created source: {source_name}")

        print(f"\nDone: {created} created, {updated} updated.")
        if skipped:
            print(f"Skipped (no matching ReportMaster row — run seed_reporting_framework.py first): {skipped}")


if __name__ == "__main__":
    run()
