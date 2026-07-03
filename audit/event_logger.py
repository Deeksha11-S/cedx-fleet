import json
from pathlib import Path
from datetime import datetime


class EventLogger:

    def __init__(self):
        self.events = []
        self.sequence = 0  # was 1, must start at 0

    def log(self, actor, action, record_id=None):

        self.events.append({

            "seq": self.sequence,
            "ts": datetime.utcnow().isoformat(),
            "actor": actor,
            "action": action,
            "record_id": record_id

        })

        self.sequence += 1

    def export(self):
        Path("out").mkdir(exist_ok=True)

        with open("out/events.json", "w", encoding="utf-8") as f:
            json.dump(
                self.events,
                f,
                indent=4
            )

        return self.events

    def get_events(self):
        return self.events