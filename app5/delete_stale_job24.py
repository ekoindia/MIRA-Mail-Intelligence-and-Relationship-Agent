import sys
sys.path.insert(0, ".")

from services.gmail_auth import get_gmail_client

RECIPIENTS = [
    "mgrfir5.zodeo@sbi.co.in", "mgrfir4.zomuz@sbi.co.in", "cmfir7.zomuz@sbi.co.in",
    "mgrfir4.zodeo@sbi.co.in", "mgrfir2.zomuz@sbi.co.in", "mgrfir2.zoran@sbi.co.in",
    "mgrfir4.zoran@sbi.co.in", "mgrfir5.zoran@sbi.co.in", "mgrfir3.zodeo@sbi.co.in",
    "mgrfir5.zomuz@sbi.co.in", "mgrfir3.zoran@sbi.co.in", "mgrfi.rbosolapur@sbi.co.in",
    "mgrfi.rbodhule@sbi.co.in", "mgrfi.nashikrural@sbi.co.in", "Rmrbo4.Kalyan@sbi.co.in",
    "mgrfi.rbonashik@sbi.co.in", "rmrbo2.thanewestern@sbi.co.in", "mgrfi.rbopunerural@sbi.co.in",
    "cmfir5.zoroh@sbi.co.in", "Cmfir2.zopkl@sbi.co.in", "cmfi.rbo5pkl@sbi.co.in",
    "CMFIR2.ZOROH@SBI.CO.IN", "CMFIR1.ZOROH@SBI.CO.IN",
]
RECIPIENTS_CF = {r.strip().casefold() for r in RECIPIENTS}
SUBJECT_MARKER = "Social Security Scheme & Account Opening"
DATE_MARKER = "26-Jul-2026"  # Previous_Date rendered when job 24 was created on 2026-07-27

service = get_gmail_client()

matched = []
page_token = None
while True:
    resp = service.users().drafts().list(userId="me", maxResults=100, pageToken=page_token).execute()
    for d in resp.get("drafts", []):
        draft_id = d["id"]
        msg = service.users().drafts().get(userId="me", id=draft_id, format="metadata").execute()
        headers = {h["name"].lower(): h["value"] for h in msg.get("message", {}).get("payload", {}).get("headers", [])}
        subject = headers.get("subject", "")
        to = headers.get("to", "")
        if SUBJECT_MARKER in subject and DATE_MARKER in subject:
            to_cf = to.strip().casefold()
            if any(r in to_cf for r in RECIPIENTS_CF):
                matched.append((draft_id, to, subject))
    page_token = resp.get("nextPageToken")
    if not page_token:
        break

print(f"Found {len(matched)} stale job-24 drafts to delete:")
for draft_id, to, subject in matched:
    print(" -", draft_id, to)

for draft_id, to, subject in matched:
    service.users().drafts().delete(userId="me", id=draft_id).execute()

print(f"\nDeleted {len(matched)} drafts.")
