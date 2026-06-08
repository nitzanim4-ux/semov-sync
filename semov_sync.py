"""
semov_sync.py
-------------
מושך שתי רשימות מסמוב, משווה, ושולח מייל עם מי שחסר ברשימה המשתנה.
"""

import os
import smtplib
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

# ── הגדרות (נטענות מ-Environment Variables) ──────────────────────────────────
SEMOV_API_KEY        = os.environ["SEMOV_API_KEY"]
FIXED_LIST_ID        = os.environ["FIXED_LIST_ID"]      # רשימה קובעת
VARIABLE_LIST_ID     = os.environ["VARIABLE_LIST_ID"]   # רשימה משתנה

EMAIL_SENDER         = os.environ["EMAIL_SENDER"]
EMAIL_PASSWORD       = os.environ["EMAIL_PASSWORD"]      # App Password של Gmail
EMAIL_RECIPIENT      = os.environ["EMAIL_RECIPIENT"]
SMTP_HOST            = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT            = int(os.environ.get("SMTP_PORT", "587"))

SEMOV_BASE_URL       = "https://rest.smoove.io/v1"      # עדכן אם שונה

# ── פונקציות Semov API ────────────────────────────────────────────────────────

def get_contacts(list_id: str) -> list[dict]:
    """מושך את כל הקונטקטים מרשימה נתונה (pagination אוטומטי)."""
    headers = {"Authorization": f"Bearer {SEMOV_API_KEY}"}
    contacts, page = [], 1

    while True:
        resp = requests.get(
            f"{SEMOV_BASE_URL}/Lists/{list_id}/Contacts",
            headers=headers,
            params={"page": page, "itemsPerPage": 100},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        # Smoove מחזיר רשימה ישירה או אובייקט עם שדה נתונים
        if isinstance(data, list):
            batch = data
        else:
            batch = data.get("contacts") or data.get("data") or data.get("results") or []

        if not batch:
            break

        contacts.extend(batch)

        # אם Smoove החזיר רשימה ישירה – בדוק אם יש עוד עמודים
        if isinstance(data, list):
            if len(batch) < 100:
                break  # פחות מ-100 תוצאות = עמוד אחרון
            page += 1
            continue

        # עצור אם אין עמוד הבא
        total_pages = data.get("total_pages") or data.get("meta", {}).get("total_pages", 1)
        if page >= total_pages:
            break
        page += 1

    print(f"  → נמצאו {len(contacts)} קונטקטים ברשימה {list_id}")
    return contacts

# ── לוגיקת השוואה ─────────────────────────────────────────────────────────────

def normalize_phone(raw: str) -> str:
    """מנקה מספר טלפון: מסיר רווחים, מקפים, סוגריים, קידומת +972."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    # +972XX → 0XX
    if digits.startswith("972") and len(digits) >= 11:
        digits = "0" + digits[3:]
    return digits

def make_key(contact: dict) -> str:
    """מפתח זיהוי: מספר טלפון נייד מנורמל."""
    raw = (
        contact.get("cellPhone")
        or contact.get("mobile")
        or contact.get("phone_mobile")
        or contact.get("cell")
        or contact.get("phone")
        or ""
    )
    return normalize_phone(raw.strip())

def find_missing(fixed: list[dict], variable: list[dict]) -> list[dict]:
    """מחזיר קונטקטים שנמצאים בקובעת אך לא במשתנה."""
    variable_keys = {make_key(c) for c in variable}
    return [c for c in fixed if make_key(c) not in variable_keys]

# ── בניית המייל ───────────────────────────────────────────────────────────────

def build_email_html(missing: list[dict]) -> str:
    today = datetime.now().strftime("%d/%m/%Y")

    rows = ""
    for c in missing:
        first   = c.get("firstName") or c.get("first_name") or ""
        last    = c.get("lastName") or c.get("last_name") or ""
        company = c.get("company") or c.get("Company") or ""
        phone   = (
            c.get("cellPhone") or c.get("mobile") or c.get("phone_mobile")
            or c.get("cell") or c.get("phone") or ""
        )
        rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;">{first}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;">{last}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;">{company}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;direction:ltr;text-align:left;">{phone}</td>
        </tr>"""

    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;max-width:700px;margin:auto;">
      <h2 style="color:#1a73e8;">דוח חוסרים הדרכת נגישות</h2>
      <p>נמצאו <strong>{len(missing)}</strong> אנשי קשר הנמצאים ברשימה הקובעת אך <u>אינם</u> ברשימה המשתנה:</p>
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <thead>
          <tr style="background:#1a73e8;color:#fff;">
            <th style="padding:10px 12px;text-align:right;">שם פרטי</th>
            <th style="padding:10px 12px;text-align:right;">שם משפחה</th>
            <th style="padding:10px 12px;text-align:right;">חברה</th>
            <th style="padding:10px 12px;text-align:right;">טלפון נייד</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="margin-top:24px;font-size:12px;color:#888;">
        נוצר אוטומטית על ידי semov_sync · {today}
      </p>
    </body></html>
    """

def build_email_text(missing: list[dict]) -> str:
    today = datetime.now().strftime("%d/%m/%Y")
    lines = ["דוח חוסרים הדרכת נגישות", f"סה\"כ: {len(missing)} חסרים\n"]
    lines += [
        f"{c.get('firstName') or c.get('first_name','')} {c.get('lastName') or c.get('last_name','')} | {c.get('company','')} | "
        f"{c.get('cellPhone') or c.get('mobile') or c.get('phone_mobile') or c.get('cell') or c.get('phone','')}"
        for c in missing
    ]
    return "\n".join(lines)

# ── שליחת המייל ───────────────────────────────────────────────────────────────

def send_email(missing: list[dict]):
    today = datetime.now().strftime("%d/%m/%Y")
    subject = "דוח חוסרים הדרכת נגישות"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECIPIENT

    msg.attach(MIMEText(build_email_text(missing), "plain", "utf-8"))
    msg.attach(MIMEText(build_email_html(missing), "html",  "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())

    print(f"  → מייל נשלח בהצלחה אל {EMAIL_RECIPIENT}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("⏳ שולף רשימות מסמוב...")
    print(f"  → רשימה קובעת ID: [{FIXED_LIST_ID}]")
    print(f"  → רשימה משתנה ID: [{VARIABLE_LIST_ID}]")
    print(f"  → האם זהים: {FIXED_LIST_ID == VARIABLE_LIST_ID}")
    fixed    = get_contacts(FIXED_LIST_ID)
    variable = get_contacts(VARIABLE_LIST_ID)
    # הדפס דוגמה של קונטקט ראשון לבדיקת שמות שדות
    if fixed:
        print(f"  → דוגמת קונטקט מרשימה קובעת: {list(fixed[0].keys())}")
        print(f"  → טלפון בדוגמה: {fixed[0].get('cellPhone') or fixed[0].get('mobile') or fixed[0].get('phone','לא נמצא')}")

    print("🔍 משווה רשימות...")
    missing = find_missing(fixed, variable)
    print(f"  → {len(missing)} חסרים נמצאו")

    if not missing:
        print("✅ אין חסרים השבוע – לא נשלח מייל.")
        return

    print("📧 שולח מייל...")
    send_email(missing)
    print("✅ סיום.")

if __name__ == "__main__":
    main()
