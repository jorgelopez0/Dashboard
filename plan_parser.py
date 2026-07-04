"""Parse the Obsidian training plan markdown file."""
import re
from datetime import date, timedelta
from pathlib import Path

PLAN_PATH = r"C:\Users\jorge\Documents\Obsidian Vault\10_Proyectos\Valencia Media Maratón.md"

ES_MONTHS = {
    "Ene": 1, "Feb": 2, "Mar": 3, "Abr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Ago": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dic": 12,
}
ES_DAYS = {
    "Lunes": 0, "Martes": 1, "Miércoles": 2, "Jueves": 3,
    "Viernes": 4, "Sábado": 5, "Domingo": 6,
}
SESSION_ICONS = {
    "intervals": "⚡", "fartlek": "🔀", "tempo": "🎯",
    "long_run": "🏔️", "easy": "🟢", "activation": "✨",
    "run": "🏃", "gym": "💪", "bike": "🚴", "rest": "😴", "race": "🏅",
}
SESSION_COLORS = {
    "intervals": "danger", "fartlek": "warning", "tempo": "orange",
    "long_run": "primary", "easy": "success", "activation": "info",
    "run": "light", "gym": "secondary", "bike": "secondary", "rest": "dark", "race": "strava",
}
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF️]+"
)


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text).strip()


def _parse_date(day_s, month_s, ref_year=None) -> date | None:
    m = ES_MONTHS.get(month_s)
    if not m:
        return None
    y = ref_year or date.today().year
    try:
        d = date(y, m, int(day_s))
        # Push to next year if the date is more than 6 months in the past
        if (date.today() - d).days > 180:
            d = date(y + 1, m, int(day_s))
        return d
    except ValueError:
        return None


GYM_KEYWORDS = ("rutina", "pecho", "espalda", "pierna", "piernas",
                "abs", "core", "hombro", "hombros", "brazo", "brazos", "gluteo", "gluteos")


def _session_type(desc: str) -> str:
    dl = desc.lower()
    if "descanso" in dl:
        return "rest"
    if "bici" in dl:
        return "bike"
    if any(k in dl for k in GYM_KEYWORDS) and "km" not in dl:
        return "gym"
    if "fartlek" in dl:
        return "fartlek"
    if re.search(r"\dx\d", dl):
        return "intervals"
    if "tempo" in dl:
        return "tempo"
    if "tirada" in dl:
        return "long_run"
    if "activación" in dl or "rectas" in dl:
        return "activation"
    if "suave" in dl or "rodaje" in dl:
        return "easy"
    if "21k" in dl or "maratón" in dl.replace("a tope", ""):
        return "race"
    if "km" in dl:
        return "run"
    return "other"


def _is_running(t: str) -> bool:
    return t in ("intervals", "fartlek", "tempo", "long_run", "easy", "activation", "run", "race")


def _target_km(desc: str):
    m = re.search(r"(\d+)\s*km", desc)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+)x(\d+)m", desc)
    if m:
        return round(int(m.group(1)) * int(m.group(2)) / 1000, 1)
    return None


def parse_plan(path=PLAN_PATH) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8")

    # Find phase names by scanning ## headings
    phase_positions: list[tuple[int, str]] = []
    for m in re.finditer(r"^## (.+)$", text, re.MULTILINE):
        phase_positions.append((m.start(), m.group(1).strip()))

    def phase_at(pos: int) -> str:
        ph = ""
        for ppos, pname in phase_positions:
            if ppos < pos:
                ph = pname
        return ph

    week_re = re.compile(
        r"^### (Semana \d+\s·\s\d+(?:\s\w+)?\s[–\-]\s\d+\s\w+.*?)$\n((?:(?!###).*\n?)*)",
        re.MULTILINE,
    )

    weeks = []
    for m in week_re.finditer(text):
        header = m.group(1).strip()
        body   = m.group(2)
        pos    = m.start()

        # Parse week number and dates from header
        # Handles both "29 Jun – 5 Jul" and "6 – 12 Jul" (implicit same month)
        wm = re.search(
            r"Semana (\d+)\s·\s(\d+)(?:\s(\w+))?\s[–\-]\s(\d+)\s(\w+)",
            header,
        )
        if not wm:
            continue
        wnum       = int(wm.group(1))
        end_month  = wm.group(5)
        start_month = wm.group(3) or end_month  # fall back to end month if omitted
        end_date   = _parse_date(wm.group(4), end_month)
        start_date = _parse_date(wm.group(2), start_month,
                                 ref_year=end_date.year if end_date else None)

        # Notes/badges from header tail
        note = re.sub(r"Semana \d+\s·\s\d+\s\w+\s[–\-]\s\d+\s\w+", "", header).strip()

        # Parse tasks
        sessions = []
        for line in body.split("\n"):
            tm = re.match(r"\s*-\s+\[([ xX])\]\s+(.+)", line)
            if not tm:
                continue
            done = tm.group(1).lower() == "x"
            full = _strip_emoji(tm.group(2).strip())

            dm = re.match(r"(\w+)\s+·\s+(.*)", full)
            if dm:
                day_name  = dm.group(1)
                task_desc = dm.group(2).strip()
            else:
                day_name  = ""
                task_desc = full

            offset = ES_DAYS.get(day_name, 0)
            sdate  = start_date + timedelta(days=offset) if start_date else None

            # Un mismo día puede combinar 2 actividades distintas (p.ej. carrera + pesas,
            # o pesas + bici) separadas por " + " — se listan como sesiones independientes.
            parts = [p.strip() for p in re.split(r"\s\+\s", task_desc) if p.strip()]
            for part in parts:
                stype = _session_type(part)
                sessions.append({
                    "day":       day_name,
                    "offset":    offset,
                    "date":      sdate.isoformat() if sdate else None,
                    "desc":      part,
                    "full_desc": full,
                    "done":      done,
                    "type":      stype,
                    "color":     SESSION_COLORS.get(stype, "secondary"),
                    "icon":      SESSION_ICONS.get(stype, "📋"),
                    "is_running": _is_running(stype),
                    "target_km": _target_km(part),
                })

        done_count = sum(1 for s in sessions if s["done"])
        run_km = sum(s["target_km"] for s in sessions if s["is_running"] and s["target_km"])

        weeks.append({
            "num":          wnum,
            "header":       header,
            "note":         note,
            "phase":        phase_at(pos),
            "start":        start_date.isoformat() if start_date else None,
            "end":          end_date.isoformat() if end_date else None,
            "sessions":     sessions,
            "done_count":   done_count,
            "total_count":  len(sessions),
            "run_km":       round(run_km, 1),
            "run_sessions": sum(1 for s in sessions if s["is_running"]),
            "pct_done":     round(done_count / len(sessions) * 100) if sessions else 0,
        })

    return weeks


def current_week(weeks=None) -> dict | None:
    if weeks is None:
        weeks = parse_plan()
    today = date.today()
    for w in weeks:
        if w["start"] and w["end"]:
            if date.fromisoformat(w["start"]) <= today <= date.fromisoformat(w["end"]):
                return w
    return weeks[0] if weeks else None


def current_week_number(weeks=None) -> int:
    w = current_week(weeks)
    return w["num"] if w else 1


def today_sessions(weeks=None) -> list[dict]:
    """Todas las sesiones de hoy (un mismo día puede tener carrera + pesas, o pesas + bici)."""
    w = current_week(weeks)
    if not w:
        return []
    today_s = date.today().isoformat()
    return [s for s in w["sessions"] if s.get("date") == today_s]


def upcoming_sessions(n=4, weeks=None) -> list[dict]:
    if weeks is None:
        weeks = parse_plan()
    today = date.today()
    result = []
    for w in weeks:
        for s in w["sessions"]:
            sd = s.get("date")
            if sd and not s["done"] and date.fromisoformat(sd) >= today:
                result.append({**s, "week_num": w["num"]})
                if len(result) >= n:
                    return result
    return result
