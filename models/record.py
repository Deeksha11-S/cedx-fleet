from dataclasses import dataclass

@dataclass
class Record:
    id: str
    owner: str
    deadline: str
    category: str
    notes: str
    version: int
    amount: float | None
    superseded: bool = False