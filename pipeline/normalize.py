from models.record import Record

def normalize_record(record: Record) -> Record:
    record.owner = record.owner.strip().lower()
    record.category = record.category.strip().upper()
    record.notes = record.notes.strip()

    return record