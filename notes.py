import json
from pathlib import Path
from datetime import datetime

NOTES_PATH = Path(__file__).parent / "notes.json"


def load_notes() -> list:
    if not NOTES_PATH.exists():
        return []
    try:
        return json.loads(NOTES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(notes: list):
    NOTES_PATH.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")


def add_note(text: str):
    text = text.strip()
    if not text:
        return
    notes = load_notes()
    next_id = max((n["id"] for n in notes), default=0) + 1
    notes.append({"id": next_id, "text": text, "done": False, "created": datetime.now().isoformat()})
    _save(notes)


def toggle_note(note_id: int):
    notes = load_notes()
    for n in notes:
        if n["id"] == note_id:
            n["done"] = not n["done"]
    _save(notes)


def delete_note(note_id: int):
    notes = load_notes()
    notes = [n for n in notes if n["id"] != note_id]
    _save(notes)
