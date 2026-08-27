"""
Generalized "one email per recipient, combining every automated report
mapped to that recipient's org level for a given frequency."

Account Opening + Social Security Scheme already used a hand-written
version of this pattern (see report_aggregation_service.
aggregate_account_opening_and_sss + MERGED_INTO_OTHER_REPORT) because they
always share the exact same org_levels. That one-off approach doesn't
generalize: per the current report mapping, RBO/LHO/Corporate Center/Branch
each need a DIFFERENT combination of reports in their single Weekly email,
and the mapping has already changed twice in one session — hand-writing a
merge function per combination doesn't scale. This module computes the
combination dynamically from each ReportMaster's own org_levels field, so
the next mapping change (adding/removing a report from a level) needs no
code change here, only a re-check of which digest template covers which
levels.

Only reports with a real aggregator (see _UNIT_AGGREGATORS below) are ever
included in a digest — the same "fully paused, not sent with placeholder
text" policy already applied to NOT_YET_AUTOMATED_REPORTS elsewhere. A
level with zero automated reports for a frequency raises ValueError (no
email at all, nothing to skip more gracefully into).

Variable namespacing: when 2+ report "units" combine into one email, each
unit's context keys are prefixed (see _UNIT_AGGREGATORS' prefix column) to
avoid real collisions confirmed while designing this (e.g. Loan Lead
Generation and Inactive CSPs both emit "Total_CSP_Count"; CSP Income Impact
and DFS Incentive Slab both emit "CSP_Count"). When only ONE automated
report applies to a level (e.g. Branch only ever gets Loan Lead Generation
today), no prefix is applied and that report's own existing standalone
template/variable names keep working unchanged.
"""
from __future__ import annotations

import json
from datetime import date

from database.models import DistributionJob, EmailLog, EmailStatus, EmailTemplate, ReportMaster, ReportUpload
from database.org_models import OrgLevel
from database.report_source_models import ReportSource
from services.calling_sheet_freshness_service import check_freshness
from services.calling_sheet_service import load_calling_sheet
from services.distribution_service import ResolvedRecipient, create_distribution_job
from services.email_service import run_distribution_job
from services.recipient_resolution_service import resolve_recipients_for_levels
from services.report_aggregation_service import (
    MERGED_INTO_OTHER_REPORT,
    aggregate_account_opening_and_sss,
    aggregate_csp_income_impact,
    aggregate_dfs_incentive_slab,
    aggregate_inactive_csps,
    aggregate_loan_lead_generation,
    filter_for_recipient,
)
from services.team_calling_summary_service import get_sbi_kiosk_growth_context
from services.growth_service import apply_growth, is_first_monday_of_month, load_two_previous_contexts
from services.report_source_service import fetch_any_report
from services.snapshot_service import save_drafted_report_snapshot
from utils.logger import get_logger

logger = get_logger(__name__)

# report_name -> (aggregator_fn, prefix | None). None = already self-
# namespaced (Account Opening's own function returns AO_/SSS_ keys since it
# secretly covers 2 reports at once) — never gets an extra prefix layered
# on top even when combined with other units.
_UNIT_AGGREGATORS: dict[str, tuple] = {
    "Account Opening (Daily)": (aggregate_account_opening_and_sss, None),
    "Account Opening (Weekly)": (aggregate_account_opening_and_sss, None),
    "Loan Lead Generation (Weekly)": (aggregate_loan_lead_generation, "LL"),
    "Inactive CSPs (Weekly)": (aggregate_inactive_csps, "INACT"),
    "DFS Incentive Slab (Monthly)": (aggregate_dfs_incentive_slab, "DFS"),
    "CSP Income Impact (Monthly)": (aggregate_csp_income_impact, "INCOME"),
    # Single fixed recipient (SBI Kiosk, a Corporate-Center-level org_unit
    # row) — always the only Daily report at that level, so no prefix
    # needed. Added 2026-08-27, see [[project_sbi_kiosk_growth_report]].
    "SBI Kiosk — Onboarding & Growth (Daily)": (get_sbi_kiosk_growth_context, None),
}

# Only needed for levels combining 2+ automated reports — the single-report
# case reuses that report's own existing default_template_id unprefixed.
# Named "{Frequency} {Level} Update" throughout, matching "Daily RBO
# Update" — the name alone tells you which level's combined email it is.
MULTI_REPORT_DIGEST_TEMPLATE_NAMES: dict[tuple[str, str], str] = {
    ("Weekly", OrgLevel.RBO.value): "Weekly RBO Update",
    ("Weekly", OrgLevel.LHO.value): "Weekly LHO Update",
    ("Weekly", OrgLevel.CORP.value): "Weekly Corporate Center Update",
    ("Monthly", OrgLevel.LHO.value): "Monthly LHO Update",
    ("Monthly", OrgLevel.CORP.value): "Monthly Corporate Center Update",
}

ALL_LEVELS = [OrgLevel.RBO, OrgLevel.LHO, OrgLevel.CORP, OrgLevel.BRANCH, OrgLevel.AO, OrgLevel.INTERNAL]


def reports_for_level(db, frequency: str, level: OrgLevel) -> list[ReportMaster]:
    """Every ReportMaster of this frequency whose org_levels includes this
    level — the full configured mapping, regardless of automation status.
    Used for display (Reports page) as well as the basis for the automated
    subset below."""
    reports = (
        db.query(ReportMaster)
        .filter(ReportMaster.frequency == frequency, ReportMaster.org_levels.isnot(None))
        .order_by(ReportMaster.report_name)
        .all()
    )
    out = []
    for r in reports:
        levels = [v.strip() for v in (r.org_levels or "").split(",") if v.strip()]
        if level.value in levels:
            out.append(r)
    return out


def is_automated(report_name: str) -> bool:
    """Strict membership check — used for routing (which units actually
    feed _combined_context, and whether a level needs the single-report or
    multi-report template lookup). Deliberately does NOT count a report
    that's merged into another one's aggregator (see is_effectively_
    automated below) — Account Opening's own function already covers
    Social Security Scheme internally, so counting SSS as a second unit
    here would double the "how many units for this level" count and can
    route Daily/RBO (1 real unit) into the multi-report template lookup by
    mistake."""
    return report_name in _UNIT_AGGREGATORS


def is_effectively_automated(report_name: str) -> bool:
    """Display-purposes check: True for a literal aggregator unit OR a
    report merged into one (e.g. Social Security Scheme is fully covered
    by Account Opening's combined aggregator — it isn't "paused", it just
    doesn't get counted as its own routing unit). Use this wherever a
    report's automated/paused status is shown to a user; use is_automated
    for anything that decides which units get sent."""
    if is_automated(report_name):
        return True
    target = MERGED_INTO_OTHER_REPORT.get(report_name)
    return target is not None and is_automated(target)


def automated_reports_for_level(db, frequency: str, level: OrgLevel) -> list[ReportMaster]:
    """The subset of reports_for_level that has a real aggregator — the
    exact, current set of reports that belong in this level's single
    combined email (not-yet-automated reports are configured but paused,
    same policy as NOT_YET_AUTOMATED_REPORTS elsewhere)."""
    return [r for r in reports_for_level(db, frequency, level) if is_automated(r.report_name)]


def display_report_list(reports: list[ReportMaster]) -> list[dict]:
    """Reports for one (frequency, level), collapsed for display: a report
    merged into another one's email (Social Security Scheme into Account
    Opening) is folded into a single "X & Y" entry instead of showing as
    its own separate, misleadingly-paused-looking item — it's fully
    covered by the anchor's aggregator, just sent under one subject line.
    Returns [{id, name, automated}], order-independent regardless of which
    of the pair appears first in `reports`."""
    by_name = {r.report_name: r for r in reports}
    consumed: set[str] = set()
    entries: list[dict] = []
    for r in reports:
        if r.report_name in consumed:
            continue
        # This report merges INTO another one that's also in this list —
        # it'll be folded into that anchor's entry below, skip it here.
        if r.report_name in MERGED_INTO_OTHER_REPORT and MERGED_INTO_OTHER_REPORT[r.report_name] in by_name:
            continue
        mergers = [
            name for name, dest in MERGED_INTO_OTHER_REPORT.items()
            if dest == r.report_name and name in by_name
        ]
        consumed.update(mergers)
        consumed.add(r.report_name)
        display_name = " & ".join([r.report_name, *mergers]) if mergers else r.report_name
        entries.append({"id": r.id, "name": display_name, "automated": is_effectively_automated(r.report_name)})
    return entries


_LOAN_LEAD_REPORT = "Loan Lead Generation (Weekly)"

# The headline number each report unit contributes to an email. A recipient
# for whom every one of these is zero would receive a mail that reads 0
# everywhere — no achievement, no leads, nothing inactive — so they are
# skipped entirely rather than drafted, per explicit instruction.
#
# This generalises the older loan-lead-only rule to every level. Branch's
# digest carries only Leads_Generated, so its behaviour is unchanged (362 of
# 375 Branch recipients were already being skipped this way); RBO/LHO/
# Corporate Center now get the same treatment instead of receiving an
# all-zero email.
#
# Note this is deliberately about ACTIVITY, not roster size: a recipient with
# CSPs on the books but nothing achieved is exactly the all-zero case being
# skipped. Keys absent from a level's context are ignored, so each level is
# judged only on the metrics it actually reports.
_ACTIVITY_METRIC_KEYS = (
    "AO_Weekly_Total", "AO_MTD_Cumulative",
    "SSS_Weekly_Total", "SSS_MTD_Cumulative",
    "INACT_Inactive_Count",
    "LL_Leads_Generated", "Leads_Generated",
)


def _as_number(value) -> float:
    """Context values arrive as ints, floats or already-formatted strings
    ("1,234", "12%"). Anything unparseable counts as zero."""
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _has_any_activity(context: dict) -> bool:
    present = [k for k in _ACTIVITY_METRIC_KEYS if k in context]
    if not present:
        # No recognised metric at all — don't silently drop the recipient on
        # the strength of a key set this function doesn't know about.
        return True
    return any(_as_number(context[k]) != 0 for k in present)


def _combined_context(reports: list[ReportMaster], recipient_df) -> dict:
    """
    Each unit's variables stay individually visible/editable in the
    Templates page (e.g. {{LL_Target}}, {{LL_Leads_Generated}} when
    combined with other reports; unprefixed when Loan Lead Generation is
    the only report for a level, e.g. Branch) — no unit is collapsed into
    an opaque server-rendered HTML blob. Loan Lead Generation's "skip this
    report when this recipient has zero leads" requirement is instead
    handled entirely in the template body via {{#if Has_Leads}}...{{/if}}
    (or {{#if LL_Has_Leads}} when prefixed) — see utils/helpers.py.
    """
    combined: dict = {}
    multi = len(reports) > 1
    for r in reports:
        aggregator, prefix = _UNIT_AGGREGATORS[r.report_name]
        context = aggregator(recipient_df)
        if multi and prefix:
            context = {f"{prefix}_{k}": v for k, v in context.items()}
        combined.update(context)
    return combined


def resolve_digest_template_id(db, frequency: str, level: OrgLevel, reports: list[ReportMaster]) -> int:
    if len(reports) == 1:
        if not reports[0].default_template_id:
            raise ValueError(f"'{reports[0].report_name}' has no template assigned yet.")
        return reports[0].default_template_id

    name = MULTI_REPORT_DIGEST_TEMPLATE_NAMES.get((frequency, level.value))
    if not name:
        raise ValueError(
            f"No combined digest template configured for {level.value} / {frequency} "
            f"(reports: {', '.join(r.report_name for r in reports)})."
        )
    template = db.query(EmailTemplate).filter(EmailTemplate.name == name).first()
    if not template:
        raise ValueError(f"Digest template '{name}' not found — create it on the Templates page first.")
    return template.id


def send_combined_digest(
    db, frequency: str, level: OrgLevel, user: dict, force_draft: bool = False,
    bypass_rbo_safety_net: bool = False,
) -> dict:
    """
    Fetch the latest calling-sheet data and send/draft ONE combined email
    per recipient at this org level, covering every automated report
    mapped to that level for this frequency. Raises ValueError (same
    skip-not-fail contract as report_send_service.send_report_now) when
    there's nothing to send: no automated reports for this level, no
    recipients, or the calling sheet hasn't refreshed since it was last
    confirmed fresh.
    """
    is_fresh, freshness_reason = check_freshness(db)
    if not is_fresh:
        raise ValueError(freshness_reason)

    reports = automated_reports_for_level(db, frequency, level)
    if not reports:
        raise ValueError(f"No automated reports mapped to {level.value} for {frequency} yet.")

    template_id = resolve_digest_template_id(db, frequency, level, reports)

    # All of this level's reports are backed by the same live Calling
    # Sheet — one representative fetch (the first report's own configured
    # source) is enough to refresh the underlying data and get a valid
    # ReportUpload row to anchor the DistributionJob on.
    anchor = reports[0]
    source = (
        db.query(ReportSource)
        .filter(ReportSource.report_master_id == anchor.id, ReportSource.is_active.is_(True))
        .first()
    )
    if source:
        fetch_result = fetch_any_report(db, source, triggered_by="manual")
        if not fetch_result["success"]:
            raise ValueError(f"Fetch failed: {fetch_result['error']}")

    latest_upload = (
        db.query(ReportUpload)
        .filter(ReportUpload.report_master_id == anchor.id)
        .order_by(ReportUpload.uploaded_at.desc())
        .first()
    )
    if not latest_upload:
        raise ValueError("No data available — connect a source on the Scheduler page first.")

    refs = resolve_recipients_for_levels(db, [level])
    if not refs:
        raise ValueError("No recipients resolved yet.")

    recipients = [
        ResolvedRecipient(
            name=r.name, email=r.email, recipient_type=r.level,
            lho_name=r.name if r.level == OrgLevel.LHO.value else None,
            cc_emails=r.cc_emails,
        )
        for r in refs
    ]

    # Same safety net as the per-report path: force draft-only whenever
    # RBO/AO recipients are included, regardless of the anchor report's
    # stored Draft Only / Send Directly setting. bypass_rbo_safety_net lifts
    # this ONLY for the callers that explicitly opt in (currently the
    # weekly auto-send cycle, per explicit instruction on 2026-08-24) — the
    # manual Scheduler-page button keeps this safety net by default.
    if not bypass_rbo_safety_net and any(
        r.recipient_type in (OrgLevel.RBO.value, OrgLevel.AO.value) for r in recipients
    ):
        force_draft = True

    job: DistributionJob = create_distribution_job(
        db, upload_id=latest_upload.id, template_id=template_id,
        recipients=recipients, created_by_id=user["id"], created_by_username=user["username"],
        is_scheduled_run=False,
    )

    df = load_calling_sheet()
    # Whenever Loan Lead Generation is the ONLY report in this level's
    # email (Branch today), a recipient with zero leads this month would
    # get a completely empty email — skip them entirely rather than draft/
    # send nothing, per explicit instruction. For levels with other
    # reports too (RBO/LHO/Corporate Center), a zero-lead recipient still
    # gets their email; only the Loan Lead section is omitted, via the
    # template's own {{#if Has_Leads}}/{{#if LL_Has_Leads}} block.
    loan_lead_only = len(reports) == 1 and reports[0].report_name == _LOAN_LEAD_REPORT

    # Week-over-week comparison, Weekly only. Loaded once per run rather than
    # per recipient: it's the same snapshots for everyone, matched by email
    # inside apply_growth. TWO prior snapshots are loaded (not just one) so
    # cumulative MTD metrics like Account Opening / SSS can be de-cumulated
    # into an isolated week's activity — see growth_service.py's docstring.
    today = date.today()
    prev_snapshot_date, prev_contexts = (None, {})
    prev2_snapshot_date, prev2_contexts = (None, {})
    if frequency == "Weekly":
        prev_snapshot_date, prev_contexts, prev2_snapshot_date, prev2_contexts = (
            load_two_previous_contexts(db, before=today)
        )

    removed = 0
    for log_row in list(job.email_logs):
        recipient_df = filter_for_recipient(df, log_row.recipient_type, log_row.recipient_name, log_row.recipient_email)
        context = _combined_context(reports, recipient_df)
        if (loan_lead_only and not context.get("Has_Leads")) or not _has_any_activity(context):
            db.delete(log_row)
            removed += 1
            continue
        if frequency == "Weekly":
            email_key = (log_row.recipient_email or "").lower()
            apply_growth(
                context,
                prev_contexts.get(email_key),
                snapshot_date=prev_snapshot_date,
                current_date=today,
                previous_previous_context=prev2_contexts.get(email_key),
                previous_previous_date=prev2_snapshot_date,
                is_first_monday=is_first_monday_of_month(today),
            )
        log_row.attachment_override_path = ""
        log_row.context_override_json = json.dumps(context)
    if removed:
        job.total_recipients = max(job.total_recipients - removed, 0)
    db.flush()

    if job.total_recipients == 0:
        raise ValueError("Every recipient had all-zero data this period — nothing to send.")

    run_distribution_job(db, job.id, force_draft=force_draft)

    # Snapshot AFTER the run, so next week has a baseline. Saves the exact
    # numbers that were drafted, not a fresh sheet re-read (the sheet moves).
    # Deliberately non-fatal: a snapshot failure must not make a successfully
    # drafted report report itself as failed.
    if frequency == "Weekly":
        try:
            saved = save_drafted_report_snapshot(db, [job.id], today)
            logger.info("Weekly snapshot saved for %s: %d recipient row(s).", today, saved)
        except Exception:  # noqa: BLE001
            logger.exception("Weekly snapshot failed for job %s — report itself was unaffected.", job.id)

    logs = db.query(EmailLog).filter(EmailLog.job_id == job.id).all()
    return {
        "level": level.value,
        "reportNames": [r.report_name for r in reports],
        "jobId": job.id,
        "recipientCount": len(logs),
        "sent": sum(1 for l in logs if l.status == EmailStatus.SENT),
        "failed": sum(1 for l in logs if l.status == EmailStatus.FAILED),
        "deliveryMode": "draft" if force_draft else (anchor.delivery_mode or "draft"),
    }
