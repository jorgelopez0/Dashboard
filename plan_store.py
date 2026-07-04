"""Almacén editable del plan de entrenamientos (reemplaza al parser de Obsidian).

Cada actividad es independiente (fecha, descripción, tipo, km objetivo opcional).
Las semanas se agrupan automáticamente por fecha (lunes-domingo) — no hace falta
declararlas a mano, así que un plan.json vacío es un esqueleto válido para empezar
de cero.
"""
import json
from pathlib import Path
from datetime import date, timedelta

PLAN_PATH = Path(__file__).parent / "plan_data.json"
WEEK_TITLES_PATH = Path(__file__).parent / "plan_week_titles.json"

TYPES = ['run', 'gym', 'bike', 'rest', 'other']
TYPE_LABELS = {'run': 'Carrera', 'gym': 'Gimnasio', 'bike': 'Bici', 'rest': 'Descanso', 'other': 'Otro'}
TYPE_COLORS = {'run': 'strava', 'gym': 'secondary', 'bike': 'info', 'rest': 'dark', 'other': 'light'}
DAY_NAMES = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']


def day_name(date_iso: str) -> str:
    try:
        return DAY_NAMES[date.fromisoformat(date_iso).weekday()]
    except (ValueError, TypeError):
        return ''


def load_activities() -> list:
    if not PLAN_PATH.exists():
        return []
    try:
        return json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(activities: list):
    PLAN_PATH.write_text(json.dumps(activities, ensure_ascii=False, indent=2), encoding="utf-8")


def add_activity(date_str: str, desc: str, type_: str, target_km=None):
    desc = desc.strip()
    if not date_str or not desc:
        return
    activities = load_activities()
    next_id = max((a["id"] for a in activities), default=0) + 1
    activities.append({
        "id": next_id,
        "date": date_str,
        "desc": desc,
        "type": type_ if type_ in TYPES else 'other',
        "target_km": target_km,
    })
    _save(activities)


def update_activity(activity_id: int, date_str: str, desc: str, type_: str, target_km=None):
    activities = load_activities()
    for a in activities:
        if a["id"] == activity_id:
            a["date"] = date_str
            a["desc"] = desc.strip()
            a["type"] = type_ if type_ in TYPES else 'other'
            a["target_km"] = target_km
    _save(activities)


def delete_activity(activity_id: int):
    activities = load_activities()
    activities = [a for a in activities if a["id"] != activity_id]
    _save(activities)


def _week_bounds(d: date):
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def load_week_titles() -> dict:
    """Título opcional por semana, guardado por fecha de inicio (lunes) de esa semana."""
    if not WEEK_TITLES_PATH.exists():
        return {}
    try:
        return json.loads(WEEK_TITLES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_week_title(start_date: str, title: str):
    titles = load_week_titles()
    title = title.strip()
    if title:
        titles[start_date] = title
    else:
        titles.pop(start_date, None)
    WEEK_TITLES_PATH.write_text(json.dumps(titles, ensure_ascii=False, indent=2), encoding="utf-8")


def build_weeks(activities: list) -> list:
    """Agrupa las actividades en semanas (lunes-domingo), ordenadas cronológicamente."""
    titles = load_week_titles()
    buckets = {}
    for a in activities:
        try:
            d = date.fromisoformat(a["date"])
        except (ValueError, TypeError):
            continue
        start, end = _week_bounds(d)
        key = start.isoformat()
        buckets.setdefault(key, {"start": start, "end": end, "activities": []})
        buckets[key]["activities"].append({**a, "day": day_name(a["date"])})

    weeks = []
    for i, key in enumerate(sorted(buckets.keys()), start=1):
        b = buckets[key]
        acts = sorted(b["activities"], key=lambda a: (a["date"], a["id"]))
        weeks.append({
            "num":   i,
            "start": b["start"].isoformat(),
            "end":   b["end"].isoformat(),
            "title": titles.get(key, ''),
            "activities": acts,
        })
    return weeks


def current_week(weeks: list):
    today = date.today()
    for w in weeks:
        if date.fromisoformat(w["start"]) <= today <= date.fromisoformat(w["end"]):
            return w
    return None


def today_activities(weeks: list) -> list:
    today_s = date.today().isoformat()
    w = current_week(weeks)
    if not w:
        return []
    return [a for a in w["activities"] if a["date"] == today_s]


def upcoming_activities(weeks: list, n=4) -> list:
    today_s = date.today().isoformat()
    result = []
    for w in weeks:
        for a in w["activities"]:
            if a["date"] and not a.get("done") and a["date"] >= today_s:
                result.append({**a, "week_num": w["num"]})
                if len(result) >= n:
                    return result
    return result
