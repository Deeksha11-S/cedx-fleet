from pathlib import Path
import json
import sqlite3  # <-- added for SQLite

from email import policy
from email.parser import BytesParser

import pdfplumber

from models.record import Record
from pipeline.normalize import normalize_record
from pipeline.validate import validate_record


def parse_email(filepath) -> Record:
    """
    Parse an .eml file and convert it into a Record object.
    """

    with open(filepath, "rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)

    body = msg.get_body(preferencelist=("plain",))

    if body:
        text = body.get_content()
    else:
        text = msg.get_payload()

    data = {}

    for line in text.splitlines():

        if ":" not in line:
            continue

        key, value = line.split(":", 1)

        data[key.strip()] = value.strip()

    amount = data.get("Amount") or data.get("Value")

    record = Record(
        id=data.get("Id", ""),
        owner=data.get("Owner", ""),
        deadline=data.get("Deadline", ""),
        category=data.get("Category", ""),
        notes=data.get("Notes", ""),
        version=int(data.get("Version", 1)),
        amount=float(amount) if amount else None,
    )

    return normalize_record(record)


def parse_pdf(filepath) -> Record:
    """
    Parse a PDF file and convert it into a Record object.
    """

    text = ""

    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()

            if page_text:
                text += page_text + "\n"

    data = {}

    for line in text.splitlines():

        if ":" not in line:
            continue

        key, value = line.split(":", 1)

        data[key.strip()] = value.strip()

    amount = data.get("Amount") or data.get("Value")

    record = Record(
        id=data.get("Id", ""),
        owner=data.get("Owner", ""),
        deadline=data.get("Deadline", ""),
        category=data.get("Category", ""),
        notes=data.get("Notes", ""),
        version=int(data.get("Version", 1)),
        amount=float(amount) if amount else None,
    )

    return normalize_record(record)


def save_records_to_sqlite(records, db_path="records.db"):
    """
    Save a list of Record objects to a SQLite database.
    Creates the 'records' table if it does not exist.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create table if not exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id TEXT,
            owner TEXT,
            deadline TEXT,
            category TEXT,
            notes TEXT,
            version INTEGER,
            amount REAL,
            superseded INTEGER,
            PRIMARY KEY (id, version)
        )
    """)

    # Insert or replace records (using PRIMARY KEY conflict)
    for record in records:
        cursor.execute("""
            INSERT OR REPLACE INTO records
            (id, owner, deadline, category, notes, version, amount, superseded)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.id,
            record.owner,
            record.deadline,
            record.category,
            record.notes,
            record.version,
            record.amount,
            1 if record.superseded else 0
        ))

    conn.commit()
    conn.close()
    print(f"\n✅ Saved {len(records)} records to {db_path}")


def load_seed_data(seed_path):
    """
    Load records from feed.json and inspect files in the inbox folder.
    """

    path = Path(seed_path)

    # ---------------------------------------------------
    # Load feed.json
    # ---------------------------------------------------
    feed_file = path / "feed.json"

    with open(feed_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    print("=" * 60)
    print("FEED.JSON")
    print("=" * 60)
    print(f"Number of records: {len(data)}")

    # ---------------------------------------------------
    # Convert JSON records into Record objects
    # ---------------------------------------------------
    records = []

    latest_versions = {}

    for item in data:
        record = Record(
            id=item["id"],
            owner=item["owner"],
            deadline=item["deadline"],
            category=item["category"],
            notes=item["notes"],
            version=item["version"],
            amount=item["amount"],
        )

        # Normalize record
        record = normalize_record(record)

        if not record.owner:
            record.owner = None

        if not record.category:
            record.category = None

        if not record.deadline:
            record.deadline = None

        record.superseded = False

        if record.id in latest_versions:

            existing = latest_versions[record.id]

            if record.version > existing.version:
                existing.superseded = True
                latest_versions[record.id] = record
            else:
                record.superseded = True

        else:
            latest_versions[record.id] = record

        records.append(record)

    print(f"\nLoaded {len(records)} Record objects")

    # ---------------------------------------------------
    # Print first record
    # ---------------------------------------------------
    print("\nFirst Record\n")

    first = records[0]

    print("ID       :", first.id)
    print("Owner    :", first.owner)
    print("Deadline :", first.deadline)
    print("Category :", first.category)
    print("Amount   :", first.amount)
    print("Notes    :", first.notes)

    # ---------------------------------------------------
    # Inspect Inbox Folder
    # ---------------------------------------------------
    inbox = path / "inbox"

    print("\n" + "=" * 60)
    print("INBOX FILES")
    print("=" * 60)

    if inbox.exists():

        for file in sorted(inbox.iterdir()):

            print("\n" + "=" * 60)
            print(f"Filename : {file.name}")
            print(f"Extension: {file.suffix}")

            # ---------------- Email ----------------
            if file.suffix.lower() == ".eml":

                print("\nParsing email...")

                try:
                    email_record = parse_email(file)

                    email_record.superseded = False

                    if email_record.id in latest_versions:

                        existing = latest_versions[email_record.id]

                        if email_record.version > existing.version:
                            existing.superseded = True
                            latest_versions[email_record.id] = email_record
                        else:
                            email_record.superseded = True

                    else:
                        latest_versions[email_record.id] = email_record

                    records.append(email_record)

                    print(f"Parsed Record : {email_record.id}")
                    print(f"Owner         : {email_record.owner}")
                    print(f"Category      : {email_record.category}")
                    print(f"Amount        : {email_record.amount}")

                except Exception as e:
                    print(f"Failed to parse email: {e}")

            # ---------------- PDF ----------------
            elif file.suffix.lower() == ".pdf":

                print("\nParsing PDF...")

                try:

                    pdf_record = parse_pdf(file)

                    pdf_record.superseded = False

                    if pdf_record.id in latest_versions:

                        existing = latest_versions[pdf_record.id]

                        if pdf_record.version > existing.version:
                            existing.superseded = True
                            latest_versions[pdf_record.id] = pdf_record
                        else:
                            pdf_record.superseded = True

                    else:
                        latest_versions[pdf_record.id] = pdf_record

                    records.append(pdf_record)

                    print(f"Parsed Record : {pdf_record.id}")
                    print(f"Owner         : {pdf_record.owner}")
                    print(f"Category      : {pdf_record.category}")
                    print(f"Amount        : {pdf_record.amount}")

                except Exception as e:

                    print(f"Failed to parse PDF: {e}")

    else:
        print("Inbox folder not found.")

    print("\n" + "=" * 60)
    print("INTAKE STAGE COMPLETED")
    print("=" * 60)

    print(f"\nTotal Records Loaded : {len(records)}")

    # ---------------------------------------------------
    # Validation
    # ---------------------------------------------------
    print("\n" + "=" * 60)
    print("VALIDATION RESULTS")
    print("=" * 60)

    for record in records:

        result = validate_record(record)

        if result.valid:
            print(f"{record.id:<10} ✅ VALID")
        else:
            print(f"{record.id:<10} ❌ {result.reason_code}")

    # ---------------------------------------------------
    # Save to SQLite (NEW)
    # ---------------------------------------------------
    save_records_to_sqlite(records, db_path="records.db")   # <-- added

    # ---------------------------------------------------
    # Return loaded records
    # ---------------------------------------------------
    return records