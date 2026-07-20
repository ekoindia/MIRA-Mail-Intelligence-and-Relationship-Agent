"""
Recipient resolution and distribution job creation.

Given a ReportUpload (tied to a ReportMaster with a RecipientType), this
service resolves the recipient list -- either automatically (based on the
report's configured recipient type) or from a manual override (specific
branches / LHOs / regions) -- and builds the DistributionJob + EmailLog
rows that the email service will later send.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from database.models import (
    Branch,
    DistributionJob,
    EmailLog,
    EmailStatus,
    JobStatus,
    LHO,
    ReportUpload,
)
from services.audit_service import log_action


@dataclass
class ResolvedRecipient:
    name: str
    email: str
    recipient_type: str  # "Branch" or "LHO"
    branch_code: str | None = None
    lho_name: str | None = None
    cc_emails: str | None = None


@dataclass
class RecipientOverride:
    branch_codes: list[str] = field(default_factory=list)
    lho_names: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)


def resolve_recipients(
    db: Session,
    recipient_type: str,
    override: RecipientOverride | None = None,
) -> list[ResolvedRecipient]:
    """
    Resolve the final recipient list.

    If `override` has any values set, it takes precedence (manual override
    by branch/LHO/region). Otherwise recipients are auto-derived from the
    report's configured recipient_type (Branch / LHO / Both) across all
    active branches and LHOs.
    """
    recipients: list[ResolvedRecipient] = []
    has_override = override and (override.branch_codes or override.lho_names or override.regions)

    if has_override:
        if override.branch_codes:
            branches = db.query(Branch).filter(
                Branch.branch_code.in_(override.branch_codes), Branch.is_active.is_(True)
            ).all()
            recipients += [
                ResolvedRecipient(b.branch_name, b.branch_email, "Branch", branch_code=b.branch_code)
                for b in branches
            ]
        if override.lho_names:
            lhos = db.query(LHO).filter(LHO.lho_name.in_(override.lho_names), LHO.is_active.is_(True)).all()
            recipients += [ResolvedRecipient(l.lho_name, l.lho_email, "LHO", lho_name=l.lho_name) for l in lhos]
        if override.regions:
            branches = db.query(Branch).filter(
                Branch.region.in_(override.regions), Branch.is_active.is_(True)
            ).all()
            recipients += [
                ResolvedRecipient(b.branch_name, b.branch_email, "Branch", branch_code=b.branch_code)
                for b in branches
            ]
    else:
        if recipient_type in ("Branch", "Both"):
            branches = db.query(Branch).filter(Branch.is_active.is_(True)).all()
            recipients += [
                ResolvedRecipient(b.branch_name, b.branch_email, "Branch", branch_code=b.branch_code)
                for b in branches
            ]
        if recipient_type in ("LHO", "Both"):
            lhos = db.query(LHO).filter(LHO.is_active.is_(True)).all()
            recipients += [ResolvedRecipient(l.lho_name, l.lho_email, "LHO", lho_name=l.lho_name) for l in lhos]

    # De-duplicate by email, preserving first occurrence
    seen: set[str] = set()
    unique: list[ResolvedRecipient] = []
    for r in recipients:
        key = r.email.lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def create_distribution_job(
    db: Session,
    upload_id: int,
    template_id: int | None,
    recipients: list[ResolvedRecipient],
    created_by_id: int,
    created_by_username: str,
    is_scheduled_run: bool = False,
) -> DistributionJob:
    """Persist a DistributionJob + one EmailLog row per resolved recipient."""
    upload = db.query(ReportUpload).get(upload_id)
    if not upload:
        raise ValueError("Report upload not found.")
    if not recipients:
        raise ValueError("No recipients resolved for this distribution.")

    job = DistributionJob(
        upload_id=upload_id,
        template_id=template_id,
        status=JobStatus.DRAFT,
        total_recipients=len(recipients),
        created_by=created_by_id,
        is_scheduled_run=is_scheduled_run,
    )
    db.add(job)
    db.flush()

    for r in recipients:
        db.add(
            EmailLog(
                job_id=job.id,
                recipient_name=r.name,
                recipient_email=r.email,
                recipient_type=r.recipient_type,
                branch_code=r.branch_code,
                lho_name=r.lho_name,
                cc_emails=r.cc_emails,
                status=EmailStatus.PENDING,
            )
        )

    log_action(
        db, "CREATE_DISTRIBUTION_JOB", user_id=created_by_id, username=created_by_username,
        entity_type="DistributionJob", entity_id=job.id,
        details=f"upload_id={upload_id}, recipients={len(recipients)}, scheduled={is_scheduled_run}",
    )
    return job
