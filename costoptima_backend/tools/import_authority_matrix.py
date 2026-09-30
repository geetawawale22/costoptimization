"""Import the legacy ``Authority_Matrix.xlsx`` into CostOptima.

    python tools/import_authority_matrix.py "../Authority_Matrix.xlsx"            # dry run
    python tools/import_authority_matrix.py "../Authority_Matrix.xlsx" --apply

Reads three sheets:

    User_Data       (NAME, ID, Commodity, User Status)  -> users + commodity roles
    Persons Left    (.., Reason)                        -> deactivates those roles
    Duplicate Entry (Commodity, TO, CC)                 -> notification routing
    Master_Split / Master_headers                       -> master split rules (reference)

The workbook holds real staff names and addresses, so nothing is copied into source or
configuration - rows are created from the file at run time, and the dry run prints counts
and role names rather than personal data.

Users are created **without a usable password**. A legacy row is an authority record, not
a credential; minting a default password for 69 real accounts would create 69 known
logins. Imported users must go through the normal password-set flow before they can sign
in, and the importer never modifies an existing user's password.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Legacy 'User Status' -> CostOptima role name. Legacy statuses are the authority model;
# the CostOptima role catalogue is seeded by app/seed.py. An unmapped status is reported,
# never silently dropped or invented.
STATUS_TO_ROLE = {
    "superuser": "Super Admin",
    "hod": "HOD",
    "masteruser": "Master User",
    "pce": "PCE",
}


def _norm(value) -> str:
    return " ".join(str(value or "").split())


def _rows(ws):
    values = list(ws.values)
    if not values:
        return []
    headers = [_norm(h) for h in values[0]]
    out = []
    for row in values[1:]:
        record = {h: v for h, v in zip(headers, row) if h}
        if any(v not in (None, "") for v in record.values()):
            out.append(record)
    return out


def _split_emails(value) -> list[str]:
    if value in (None, ""):
        return []
    text = str(value).replace("\n", ";").replace(",", ";")
    return [part.strip() for part in text.split(";") if part.strip()]


def extract(path: Path) -> dict:
    wb = load_workbook(path, data_only=True)
    try:
        payload = {"users": [], "left": [], "notifications": [], "splits": [],
                   "unmapped_statuses": Counter(), "warnings": []}

        for record in _rows(wb["User_Data"]) if "User_Data" in wb.sheetnames else []:
            email = _norm(record.get("ID"))
            commodity = _norm(record.get("Commodity"))
            status = _norm(record.get("User Status"))
            if not email or not commodity:
                payload["warnings"].append("User_Data row missing ID or Commodity; skipped")
                continue
            role = STATUS_TO_ROLE.get(status.casefold())
            if role is None:
                payload["unmapped_statuses"][status] += 1
            payload["users"].append({"name": _norm(record.get("NAME")), "email": email,
                                     "commodity": commodity, "legacy_status": status,
                                     "role": role})

        for record in _rows(wb["Persons Left"]) if "Persons Left" in wb.sheetnames else []:
            email = _norm(record.get("ID"))
            if email:
                payload["left"].append({"email": email,
                                        "commodity": _norm(record.get("Commodity")),
                                        "reason": _norm(record.get("Reason")) or "Left"})

        for record in _rows(wb["Duplicate Entry"]) if "Duplicate Entry" in wb.sheetnames else []:
            commodity = _norm(record.get("Commodity"))
            if commodity:
                payload["notifications"].append({
                    "commodity": commodity,
                    "to": _split_emails(record.get("TO")),
                    "cc": _split_emails(record.get("CC"))})

        for record in _rows(wb["Master_headers"]) if "Master_headers" in wb.sheetnames else []:
            label = _norm(record.get("Colname"))
            if label:
                payload["splits"].append({
                    "file_label": label,
                    "header_mode": _norm(record.get("Header")),
                    "unique_key": _norm(record.get("Unique")),
                    "sheet_name": _norm(record.get("SheetName"))})
        return payload
    finally:
        wb.close()


def apply(payload: dict) -> dict:
    from app.database import SessionLocal
    from app.models.authority import MasterSplitRule, UserCommodityRole
    from app.models.user import NotificationRule, Role, User

    counts = Counter()
    db = SessionLocal()
    try:
        roles = {r.name.casefold(): r for r in db.query(Role).all()}
        missing_roles = sorted({u["role"] for u in payload["users"]
                                if u["role"] and u["role"].casefold() not in roles})
        if missing_roles:
            raise SystemExit(
                "These roles do not exist. Run 'python -m app.seed' first, or add them:\n  "
                + "\n  ".join(missing_roles))

        left = {(item["email"].casefold(), item["commodity"].casefold()): item
                for item in payload["left"]}
        left_any = {item["email"].casefold(): item for item in payload["left"]}

        for entry in payload["users"]:
            if entry["role"] is None:
                counts["skipped_unmapped_status"] += 1
                continue
            email = entry["email"]
            user = db.query(User).filter(User.email == email).first()
            if user is None:
                # No usable password: an authority record is not a credential.
                user = User(name=entry["name"] or email, email=email,
                            hashed_password="!", status="Active")
                db.add(user)
                db.flush()
                counts["users_created"] += 1
            role = roles[entry["role"].casefold()]
            link = (db.query(UserCommodityRole)
                    .filter(UserCommodityRole.user_id == user.id,
                            UserCommodityRole.commodity == entry["commodity"]).first())
            if link is None:
                link = UserCommodityRole(user_id=user.id, commodity=entry["commodity"])
                db.add(link)
                counts["roles_created"] += 1
            else:
                counts["roles_updated"] += 1
            link.role_id = role.id
            link.legacy_status = entry["legacy_status"]
            departure = (left.get((email.casefold(), entry["commodity"].casefold()))
                         or left_any.get(email.casefold()))
            if departure:
                link.status = "Inactive"
                link.inactive_reason = departure["reason"]
                counts["roles_deactivated"] += 1
            else:
                link.status = "Active"

        # 'Persons Left' people have already been removed from User_Data (zero overlap in
        # the supplied workbook), so this is a historical record rather than a list of
        # accounts to switch off. Import them as INACTIVE rows: discarding them would lose
        # who held authority over which commodity, which is exactly what an authority
        # audit trail is for. They are inactive, so authority_service never grants on them.
        for entry in payload["left"]:
            if not entry["commodity"]:
                counts["departures_without_commodity"] += 1
                continue
            email = entry["email"]
            user = db.query(User).filter(User.email == email).first()
            if user is None:
                user = User(name=email, email=email, hashed_password="!",
                            status="Inactive", deactivation_reason=entry["reason"])
                db.add(user)
                db.flush()
                counts["departed_users_recorded"] += 1
            link = (db.query(UserCommodityRole)
                    .filter(UserCommodityRole.user_id == user.id,
                            UserCommodityRole.commodity == entry["commodity"]).first())
            if link is None:
                # No role_id is asserted for a departed person whose status we cannot
                # map; fall back to the PCE role only when the workbook recorded one.
                fallback = roles.get("pce")
                if fallback is None:
                    counts["departures_skipped_no_role"] += 1
                    continue
                link = UserCommodityRole(user_id=user.id, commodity=entry["commodity"],
                                         role_id=fallback.id)
                db.add(link)
                counts["departed_roles_recorded"] += 1
            link.status = "Inactive"
            link.inactive_reason = entry["reason"]

        for entry in payload["notifications"]:
            rule = (db.query(NotificationRule)
                    .filter(NotificationRule.commodity == entry["commodity"]).first())
            if rule is None:
                rule = NotificationRule(commodity=entry["commodity"])
                db.add(rule)
                counts["notification_rules_created"] += 1
            else:
                counts["notification_rules_updated"] += 1
            rule.to_emails = entry["to"]
            rule.cc_emails = entry["cc"]

        for entry in payload["splits"]:
            rule = (db.query(MasterSplitRule)
                    .filter(MasterSplitRule.file_label == entry["file_label"],
                            MasterSplitRule.commodity == "*").first())
            if rule is None:
                rule = MasterSplitRule(commodity="*", file_label=entry["file_label"])
                db.add(rule)
                counts["split_rules_created"] += 1
            rule.header_mode = entry["header_mode"]
            rule.unique_key = entry["unique_key"]
            rule.sheet_name = entry["sheet_name"]

        db.commit()
        return dict(counts)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--apply", action="store_true", help="write to the database")
    args = parser.parse_args(argv)

    payload = extract(args.path)
    people = {u["email"].casefold() for u in payload["users"]}
    print(f"Authority rows      : {len(payload['users'])}")
    print(f"Distinct people     : {len(people)}")
    print(f"Commodities         : {len(sorted({u['commodity'] for u in payload['users']}))}")
    print(f"Role distribution   : {dict(Counter(u['legacy_status'] for u in payload['users']))}")
    print(f"Departures          : {len(payload['left'])}")
    print(f"Notification rules  : {len(payload['notifications'])}")
    print(f"Master split rules  : {len(payload['splits'])}")
    if payload["unmapped_statuses"]:
        print(f"UNMAPPED statuses   : {dict(payload['unmapped_statuses'])} "
              "-> add to STATUS_TO_ROLE; these rows are skipped, not guessed")
    for warning in payload["warnings"][:10]:
        print(f"  warning: {warning}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        return 0
    print("\nApplied:", apply(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
