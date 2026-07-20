from database.db import get_db
from database.models import ReportMaster
from services.recipient_resolution_service import resolve_recipient_by_ref
from database.org_models import OrgUnit

with get_db() as db:
    corp = db.query(OrgUnit).filter(OrgUnit.unit_name == "Corporate Center").first()
    ref = resolve_recipient_by_ref(db, "org", "Corporate Center", unit_id=corp.id)
    print("Real recipient ref:", ref)

    # Simulate what test_send does for a real send (no override) vs a test redirect
    is_test_redirect = False
    cc_real = None if is_test_redirect else ref.cc_emails
    is_test_redirect = True
    cc_test = None if is_test_redirect else ref.cc_emails
    print("CC when sending for real:", cc_real)
    print("CC when redirected to a test address:", cc_test)
