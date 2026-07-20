"""
Org hierarchy master data: bulk Excel import per level (Corp Center / AO /
RBO / LHO), directory read, and recipient resolution for automated
distribution. Mirrors `upload_service.import_branch_lho_master`.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from database.org_models import OrgLevel, OrgUnit
from services.audit_service import log_action
from utils.validators import is_valid_email, validate_master_excel_columns

ORG_REQUIRED_COLS = ["Unit Name", "Email"]


class OrgUploadError(Exception):
    pass


def import_org_units(db: Session, level: OrgLevel, dataframe: pd.DataFrame, uploaded_by: str) -> dict:
    """
    Import org units for a single level from a DataFrame with columns:
    Unit Name, Email, Unit Code (optional), Parent Unit Name (optional), Region (optional)

    Upserts by (level, unit_name). Parent Unit Name is resolved against any
    existing OrgUnit row regardless of level (order isn't assumed).
    """
    ok, msg = validate_master_excel_columns(list(dataframe.columns), ORG_REQUIRED_COLS)
    if not ok:
        raise OrgUploadError(msg)

    dataframe = dataframe.rename(columns={c: c.strip() for c in dataframe.columns})

    errors: list[str] = []
    created, updated = 0, 0

    for idx, row in dataframe.iterrows():
        row_num = idx + 2
        try:
            unit_name = str(row["Unit Name"]).strip() if pd.notna(row.get("Unit Name")) else ""
            email = str(row["Email"]).strip() if pd.notna(row.get("Email")) else ""
            unit_code = (
                str(row["Unit Code"]).strip()
                if "Unit Code" in dataframe.columns and pd.notna(row.get("Unit Code"))
                else None
            )
            parent_name = (
                str(row["Parent Unit Name"]).strip()
                if "Parent Unit Name" in dataframe.columns and pd.notna(row.get("Parent Unit Name"))
                else ""
            )
            region = (
                str(row["Region"]).strip()
                if "Region" in dataframe.columns and pd.notna(row.get("Region"))
                else None
            )

            if not unit_name:
                errors.append(f"Row {row_num}: Unit Name is required.")
                continue
            if not is_valid_email(email):
                errors.append(f"Row {row_num}: Invalid email '{email}'.")
                continue

            parent_obj = None
            if parent_name:
                parent_obj = db.query(OrgUnit).filter(OrgUnit.unit_name == parent_name).first()
                if not parent_obj:
                    errors.append(f"Row {row_num}: Parent unit '{parent_name}' not found (upload it first).")

            existing = db.query(OrgUnit).filter(
                OrgUnit.level == level, OrgUnit.unit_name == unit_name
            ).first()
            if existing:
                existing.email = email
                existing.unit_code = unit_code or existing.unit_code
                existing.region = region or existing.region
                if parent_obj:
                    existing.parent = parent_obj
                updated += 1
            else:
                db.add(OrgUnit(
                    level=level, unit_name=unit_name, email=email, unit_code=unit_code,
                    region=region, parent=parent_obj,
                ))
                created += 1

        except Exception as exc:  # noqa: BLE001
            errors.append(f"Row {row_num}: {exc}")

    db.flush()
    log_action(
        db, "UPLOAD_ORG_MASTER", username=uploaded_by, entity_type="OrgUnit",
        details=f"level={level.value}, +{created}/~{updated}, errors={len(errors)}",
    )

    return {"created": created, "updated": updated, "errors": errors}


_NAME_HINTS = ["unit name", "csp name", "branch name", "office name", "name"]
_EMAIL_HINTS = ["email", "e-mail", "mail id", "mail"]
_CODE_HINTS = ["code", "csp id", "branch code"]
_PARENT_HINTS = ["parent"]
_REGION_HINTS = ["region", "circle", "zone", "state"]


def _find_column(columns: list[str], hints: list[str], exclude: set[str] | None = None) -> str | None:
    exclude = exclude or set()
    lower = {c: c.strip().lower() for c in columns}
    for hint in hints:
        for col, low in lower.items():
            if col in exclude:
                continue
            if low == hint or hint in low:
                return col
    return None


def _detect_email_column(dataframe: pd.DataFrame, exclude: set[str]) -> str | None:
    """Fallback when no column is named anything email-like: scan cell
    values for '@' patterns and pick whichever column matches most."""
    best_col, best_hits = None, 0
    for col in dataframe.columns:
        if col in exclude:
            continue
        sample = dataframe[col].dropna().astype(str).head(20)
        hits = sample.str.contains("@", regex=False).sum()
        if hits > best_hits:
            best_col, best_hits = col, hits
    return best_col if best_hits > 0 else None


def import_org_units_auto(db: Session, level: OrgLevel, dataframe: pd.DataFrame, uploaded_by: str) -> dict:
    """
    Import org units from WHATEVER columns the uploaded sheet actually has —
    no fixed template required. Detects the name/email/code/parent/region
    columns by header wording first, falling back to scanning cell values
    for email-shaped text when no column is named anything "email"-like.
    """
    dataframe = dataframe.rename(columns={c: str(c).strip() for c in dataframe.columns})
    columns = list(dataframe.columns)

    email_col = _find_column(columns, _EMAIL_HINTS) or _detect_email_column(dataframe, exclude=set())
    if not email_col:
        raise OrgUploadError(
            "Couldn't find an email column in this sheet — none of the column headers "
            "mention 'email' and no column contains email-shaped values."
        )
    name_col = _find_column(columns, _NAME_HINTS, exclude={email_col})
    if not name_col:
        # Fall back to the first non-email text column.
        remaining = [c for c in columns if c != email_col]
        name_col = remaining[0] if remaining else None
    if not name_col:
        raise OrgUploadError("Couldn't find a name column in this sheet — it needs at least a name and an email column.")

    code_col = _find_column(columns, _CODE_HINTS, exclude={email_col, name_col})
    parent_col = _find_column(columns, _PARENT_HINTS, exclude={email_col, name_col})
    region_col = _find_column(columns, _REGION_HINTS, exclude={email_col, name_col})

    errors: list[str] = []
    created, updated = 0, 0

    for idx, row in dataframe.iterrows():
        row_num = idx + 2
        try:
            unit_name = str(row[name_col]).strip() if pd.notna(row.get(name_col)) else ""
            email = str(row[email_col]).strip() if pd.notna(row.get(email_col)) else ""
            unit_code = str(row[code_col]).strip() if code_col and pd.notna(row.get(code_col)) else None
            parent_name = str(row[parent_col]).strip() if parent_col and pd.notna(row.get(parent_col)) else ""
            region = str(row[region_col]).strip() if region_col and pd.notna(row.get(region_col)) else None

            if not unit_name:
                errors.append(f"Row {row_num}: name is empty.")
                continue
            if not is_valid_email(email):
                errors.append(f"Row {row_num}: invalid email '{email}'.")
                continue

            parent_obj = None
            if parent_name:
                parent_obj = db.query(OrgUnit).filter(OrgUnit.unit_name == parent_name).first()
                if not parent_obj:
                    errors.append(f"Row {row_num}: parent unit '{parent_name}' not found (upload it first).")

            existing = db.query(OrgUnit).filter(
                OrgUnit.level == level, OrgUnit.unit_name == unit_name
            ).first()
            if existing:
                existing.email = email
                existing.unit_code = unit_code or existing.unit_code
                existing.region = region or existing.region
                if parent_obj:
                    existing.parent = parent_obj
                updated += 1
            else:
                db.add(OrgUnit(
                    level=level, unit_name=unit_name, email=email, unit_code=unit_code,
                    region=region, parent=parent_obj,
                ))
                created += 1

        except Exception as exc:  # noqa: BLE001
            errors.append(f"Row {row_num}: {exc}")

    db.flush()
    log_action(
        db, "UPLOAD_ORG_MASTER", username=uploaded_by, entity_type="OrgUnit",
        details=f"level={level.value}, +{created}/~{updated}, errors={len(errors)}, "
                f"detected columns: name='{name_col}', email='{email_col}'",
    )

    return {
        "created": created, "updated": updated, "errors": errors,
        "detected_columns": {"name": name_col, "email": email_col, "code": code_col,
                              "parent": parent_col, "region": region_col},
    }


def _normalize_cc_emails(cc_emails: str | None) -> str | None:
    if not cc_emails:
        return None
    addrs = [a.strip() for a in cc_emails.split(",") if a.strip()]
    for addr in addrs:
        if not is_valid_email(addr):
            raise OrgUploadError(f"Invalid CC email address '{addr}'.")
    return ", ".join(addrs) if addrs else None


def add_org_unit(
    db: Session, level: OrgLevel, unit_name: str, email: str,
    unit_code: str | None = None, region: str | None = None, added_by: str = "",
    cc_emails: str | None = None,
) -> OrgUnit:
    """Add one recipient directly — the lightweight alternative to bulk Excel import."""
    unit_name = (unit_name or "").strip()
    email = (email or "").strip()
    if not unit_name:
        raise OrgUploadError("Name is required.")
    if not is_valid_email(email):
        raise OrgUploadError(f"Invalid email '{email}'.")
    if db.query(OrgUnit).filter(OrgUnit.level == level, OrgUnit.unit_name == unit_name).first():
        raise OrgUploadError(f"A {level.value} named '{unit_name}' already exists.")

    unit = OrgUnit(
        level=level, unit_name=unit_name, email=email,
        unit_code=(unit_code or "").strip() or None, region=(region or "").strip() or None,
        cc_emails=_normalize_cc_emails(cc_emails),
    )
    db.add(unit)
    db.flush()
    log_action(db, "ADD_ORG_UNIT", username=added_by, entity_type="OrgUnit", entity_id=unit.id,
               details=f"{level.value}: {unit_name} <{email}> cc={unit.cc_emails or '-'}")
    return unit


def update_org_unit(db: Session, unit_id: int, unit_name: str | None = None,
                     email: str | None = None, updated_by: str = "",
                     cc_emails: str | None = None) -> OrgUnit:
    unit = db.query(OrgUnit).get(unit_id)
    if not unit:
        raise OrgUploadError("Recipient not found.")
    if unit_name is not None and unit_name.strip():
        unit.unit_name = unit_name.strip()
    if email is not None:
        email = email.strip()
        if not is_valid_email(email):
            raise OrgUploadError(f"Invalid email '{email}'.")
        unit.email = email
    if cc_emails is not None:
        unit.cc_emails = _normalize_cc_emails(cc_emails)
    db.flush()
    log_action(db, "UPDATE_ORG_UNIT", username=updated_by, entity_type="OrgUnit", entity_id=unit.id,
               details=f"{unit.level.value}: {unit.unit_name} <{unit.email}> cc={unit.cc_emails or '-'}")
    return unit


def delete_org_unit(db: Session, unit_id: int, deleted_by: str = "") -> None:
    unit = db.query(OrgUnit).get(unit_id)
    if not unit:
        raise OrgUploadError("Recipient not found.")
    log_action(db, "DELETE_ORG_UNIT", username=deleted_by, entity_type="OrgUnit", entity_id=unit.id,
               details=f"{unit.level.value}: {unit.unit_name} <{unit.email}>")
    db.delete(unit)
    db.flush()


def list_org_units(db: Session, level: OrgLevel | None = None, active_only: bool = True) -> list[OrgUnit]:
    q = db.query(OrgUnit)
    if level:
        q = q.filter(OrgUnit.level == level)
    if active_only:
        q = q.filter(OrgUnit.is_active.is_(True))
    return q.order_by(OrgUnit.level, OrgUnit.unit_name).all()


def get_org_directory(db: Session) -> list[dict]:
    """Flattened rows for the Directory tab, with resolved parent name."""
    units = db.query(OrgUnit).order_by(OrgUnit.level, OrgUnit.unit_name).all()
    return [
        {
            "Level": u.level.value,
            "Unit Name": u.unit_name,
            "Unit Code": u.unit_code or "-",
            "Email": u.email,
            "Parent": u.parent.unit_name if u.parent else "-",
            "Region": u.region or "-",
            "Active": "✅" if u.is_active else "❌",
        }
        for u in units
    ]


def resolve_org_recipients(
    db: Session, level: OrgLevel, unit_ids: list[int] | None = None
) -> list[dict]:
    """Recipient list (name/email) for a level, optionally restricted to specific unit ids."""
    q = db.query(OrgUnit).filter(OrgUnit.level == level, OrgUnit.is_active.is_(True))
    if unit_ids:
        q = q.filter(OrgUnit.id.in_(unit_ids))
    units = q.all()

    seen: set[str] = set()
    recipients: list[dict] = []
    for u in units:
        key = u.email.lower().strip()
        if key and key not in seen:
            seen.add(key)
            recipients.append({
                "name": u.unit_name, "email": u.email, "level": u.level.value, "unit_id": u.id,
                "cc_emails": u.cc_emails,
            })
    return recipients


def resolve_org_recipients_multi(
    db: Session, levels: list[OrgLevel], unit_ids: list[int] | None = None
) -> list[dict]:
    """
    Recipient list across SEVERAL levels at once, de-duplicated by email.

    Many reports in the distribution matrix go to more than one level in the
    same send (e.g. "Server Issue" -> LHO + Corporate Center together), so
    this is the primary entry point auto-distribution schedules use.
    """
    seen: set[str] = set()
    recipients: list[dict] = []
    for level in levels:
        for r in resolve_org_recipients(db, level, unit_ids):
            key = r["email"].lower().strip()
            if key and key not in seen:
                seen.add(key)
                recipients.append(r)
    return recipients
