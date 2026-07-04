"""Garmin Connect data client using garth for auth, with disk-backed caching."""
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, module="garth")
import garth
from dotenv import load_dotenv

load_dotenv()

GARTH_TOKENSTORE = os.getenv("GARMIN_TOKENSTORE", str(Path.home() / ".garmin-mcp"))
CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

SHORT_TTL = 1      # today's data: prácticamente sin caché, para reflejar cada sync del reloj al instante
LONG_TTL  = 86400  # 24 h  – historical data
MED_TTL   = 21600  # 6 h   – PRs / race predictions (refreshed by Garmin ~daily)

PR_TYPE_MAP = {3: "5k", 4: "10k", 5: "half", 6: "marathon"}

ACTIVITY_GROUPS = {
    "running":  {"running", "trail_running", "track_running", "treadmill_running", "street_running"},
    "strength": {"strength_training"},
    "bike":     {"cycling", "road_biking", "mountain_biking", "gravel_cycling",
                 "track_cycling", "indoor_cycling", "virtual_ride", "cyclocross", "bmx"},
}

PESAS_LABELS = ["Push", "Pull", "Legs", "Abs"]
PESAS_LABELS_PATH = Path(__file__).parent / "pesas_labels.json"


def get_pesas_labels() -> dict:
    if not PESAS_LABELS_PATH.exists():
        return {}
    try:
        return json.loads(PESAS_LABELS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_pesas_label(activity_id, label: str):
    if label not in PESAS_LABELS:
        return
    data = get_pesas_labels()
    data[str(activity_id)] = label
    PESAS_LABELS_PATH.write_text(json.dumps(data), encoding="utf-8")

_garth_ready = False
_display_name: str = ""


# ─────────────── cache ───────────────

def _cache_path(key: str) -> Path:
    safe = key.replace("/", "_").replace(":", "-").replace(" ", "_")
    return CACHE_DIR / f"{safe}.json"

def _cget(key: str, ttl: int = SHORT_TTL):
    p = _cache_path(key)
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - d["ts"] < ttl:
                return d["val"]
        except Exception:
            pass
    return None

def _cset(key: str, val, ttl: int = SHORT_TTL):
    try:
        _cache_path(key).write_text(
            json.dumps({"ts": time.time(), "val": val}, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass

def _ttl(d: str) -> int:
    today = str(date.today())
    yesterday = str(date.today() - timedelta(days=1))
    if d == today:
        return SHORT_TTL
    if d == yesterday:
        return MED_TTL  # Garmin sync can lag into the next day, don't lock it in for 24h
    return LONG_TTL


# ─────────────── auth / init ───────────────

def _init():
    global _garth_ready, _display_name
    if _garth_ready:
        return
    garth.resume(GARTH_TOKENSTORE)
    # Read cached display name from profile.json saved by the MCP server
    profile_path = Path(GARTH_TOKENSTORE) / "profile.json"
    if profile_path.exists():
        try:
            p = json.loads(profile_path.read_text(encoding="utf-8"))
            _display_name = p.get("displayName", "")
        except Exception:
            pass
    if not _display_name:
        try:
            p = garth.connectapi("/userprofile-service/socialProfile")
            _display_name = p.get("displayName", "")
        except Exception:
            pass
    _garth_ready = True


def _dn() -> str:
    _init()
    return _display_name


def _api(path: str, params: dict | None = None):
    _init()
    return garth.connectapi(path, params=params or {})


# ─────────────── helpers ───────────────

def _fmt(d) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    return str(d)

def _pace(ms: float) -> str:
    if not ms or ms <= 0:
        return "–"
    spkm = 1000 / ms
    return f"{int(spkm // 60)}:{int(spkm % 60):02d}"

def _int(v):
    return int(round(v)) if v is not None else None


def _round1(v):
    return round(v, 1) if v is not None else None


def _hms(s: float) -> str:
    if not s:
        return "–"
    h, r = divmod(int(s), 3600)
    m, sec = divmod(r, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


# ─────────────── daily summary ───────────────

def daily_summary(d=None):
    d = _fmt(d or date.today())
    key = f"daily:{d}"
    c = _cget(key, _ttl(d))
    if c is not None:
        return c
    try:
        s = _api(f"/usersummary-service/usersummary/daily/{_dn()}", {"calendarDate": d})
        if not isinstance(s, dict):
            s = {}
    except Exception as e:
        return {"error": str(e)}
    result = {
        "date": d,
        "steps": s.get("totalSteps", 0) or 0,
        "step_goal": s.get("dailyStepGoal", 7760) or 7760,
        "step_pct": round((s.get("totalSteps", 0) or 0) / max(s.get("dailyStepGoal", 7760) or 7760, 1) * 100),
        "distance_km": round((s.get("totalDistanceMeters", 0) or 0) / 1000, 2),
        "calories": s.get("totalKilocalories", 0) or 0,
        "active_calories": s.get("activeKilocalories", 0) or 0,
        "resting_hr": s.get("restingHeartRate"),
        "min_hr": s.get("minHeartRate"),
        "max_hr_day": s.get("maxHeartRate"),
        "body_battery": s.get("bodyBatteryMostRecentValue") or s.get("endBodyBatteryValue"),
        "body_battery_high": s.get("bodyBatteryHighestValue") or s.get("peakBodyBatteryValue"),
        "body_battery_low": s.get("bodyBatteryLowestValue"),
        "floors": round(s.get("floorsAscended", 0) or 0, 1),
        "floor_goal": s.get("userFloorsAscendedGoal", 10) or s.get("dailyFloorGoal", 10) or 10,
        "floor_pct": round((s.get("floorsAscended", 0) or 0) / max(s.get("userFloorsAscendedGoal", 10) or 10, 1) * 100),
        "stress": s.get("averageStressLevel"),
        "sedentary_h": round((s.get("sedentarySeconds", 0) or 0) / 3600, 1),
        "active_min": round(((s.get("activeSeconds", 0) or 0) + (s.get("highlyActiveSeconds", 0) or 0)) / 60),
    }
    _cset(key, result, _ttl(d))
    return result


# ─────────────── steps / floors history ───────────────

def _day_stats(d: str) -> dict:
    key = f"dstats:{d}"
    c = _cget(key, _ttl(d))
    if c is not None:
        return c
    try:
        s = _api(f"/usersummary-service/usersummary/daily/{_dn()}", {"calendarDate": d})
        if not isinstance(s, dict):
            s = {}
        steps = s.get("totalSteps", 0) or 0
        goal  = s.get("dailyStepGoal", 7760) or 7760
        floors = round(s.get("floorsAscended", 0) or 0, 1)
        fgoal  = s.get("userFloorsAscendedGoal", 10) or s.get("dailyFloorGoal", 10) or 10
        result = {
            "date": d,
            "steps": steps,
            "goal": goal,
            "met": steps >= goal,
            "floors": floors,
            "floor_goal": fgoal,
            "floor_met": floors >= fgoal,
            "resting_hr": s.get("restingHeartRate"),
        }
    except Exception:
        result = {
            "date": d, "steps": 0, "goal": 7760, "met": False,
            "floors": 0, "floor_goal": 10, "floor_met": False, "resting_hr": None,
        }
    _cset(key, result, _ttl(d))
    return result


def steps_last_n(n=7):
    return [_day_stats(_fmt(date.today() - timedelta(days=i))) for i in range(n - 1, -1, -1)]


def step_streak(max_days=60):
    c = _cget("step_streak", SHORT_TTL)
    if c is not None:
        return c
    streak = 0
    for i in range(max_days):
        met = _day_stats(_fmt(date.today() - timedelta(days=i))).get("met")
        if i == 0 and not met:
            continue  # hoy no ha acabado, no rompe la racha todavía
        if met:
            streak += 1
        else:
            break
    _cset("step_streak", streak, SHORT_TTL)
    return streak


def floors_streak(max_days=60):
    c = _cget("floors_streak", SHORT_TTL)
    if c is not None:
        return c
    streak = 0
    for i in range(max_days):
        met = _day_stats(_fmt(date.today() - timedelta(days=i))).get("floor_met")
        if i == 0 and not met:
            continue  # hoy no ha acabado, no rompe la racha todavía
        if met:
            streak += 1
        else:
            break
    _cset("floors_streak", streak, SHORT_TTL)
    return streak


def resting_hr_history(days=30):
    key = f"rhr:{days}"
    c = _cget(key, SHORT_TTL)
    if c is not None:
        return c
    result = [
        {"date": r["date"], "rhr": r["resting_hr"]}
        for i in range(days - 1, -1, -1)
        if (r := _day_stats(_fmt(date.today() - timedelta(days=i)))) and r.get("resting_hr")
    ]
    _cset(key, result, SHORT_TTL)
    return result


# ─────────────── activities ───────────────

def _fmt_act(a: dict) -> dict:
    spd = a.get("averageSpeed") or 0
    return {
        "id": a.get("activityId"),
        "name": a.get("activityName", "Carrera"),
        "date": (a.get("startTimeLocal") or "")[:10],
        "datetime": a.get("startTimeLocal", ""),
        "distance_km": round((a.get("distance", 0) or 0) / 1000, 2),
        "duration": _hms(a.get("duration")),
        "duration_sec": a.get("duration", 0),
        "pace": _pace(spd),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "calories": a.get("calories"),
        "elevation": round(a.get("elevationGain") or 0),
        "vo2max": a.get("vO2MaxValue"),
        "aerobic_te": _round1(a.get("aerobicTrainingEffect")),
        "anaerobic_te": _round1(a.get("anaerobicTrainingEffect")),
        "training_label": (a.get("trainingEffectLabel") or "").replace("_", " ").title(),
        "pr": a.get("pr", False),
        "cadence": round(a.get("averageRunningCadenceInStepsPerMinute") or 0),
        "hr_z1": a.get("hrTimeInZone_1") or 0,
        "hr_z2": a.get("hrTimeInZone_2") or 0,
        "hr_z3": a.get("hrTimeInZone_3") or 0,
        "hr_z4": a.get("hrTimeInZone_4") or 0,
        "hr_z5": a.get("hrTimeInZone_5") or 0,
    }


def _fetch_activities_range(s: str, e: str, atype: str = "") -> list:
    """Paginated fetch of activities between two dates. Empty atype = all types."""
    all_acts: list = []
    offset = 0
    limit = 100
    while True:
        page = _api(
            "/activitylist-service/activities/search/activities",
            {"startDate": s, "endDate": e, "activityType": atype, "start": offset, "limit": limit},
        )
        if not isinstance(page, list) or not page:
            break
        all_acts.extend(page)
        if len(page) < limit:
            break
        offset += limit
    return all_acts


def _raw_activities_range(start, end) -> list:
    """Unfiltered raw activities for a date range, shared across group filters."""
    s, e = _fmt(start), _fmt(end)
    key = f"acts_raw:{s}:{e}"
    ttl = LONG_TTL if e < str(date.today()) else SHORT_TTL
    c = _cget(key, ttl)
    if c is not None:
        return c
    try:
        raw = _fetch_activities_range(s, e)
    except Exception:
        raw = []
    _cset(key, raw, ttl)
    return raw


def _vo2max_map(start, end):
    """Vo2max 'genérico' oficial de Garmin por fecha (mismo que muestra el reloj/app),
    para sobreescribir el valor puntual que trae cada actividad."""
    s, e = _fmt(start), _fmt(end)
    key = f"vo2map:{s}:{e}"
    ttl = LONG_TTL if e < str(date.today()) else SHORT_TTL
    c = _cget(key, ttl)
    if c is not None:
        return c
    result = {}
    try:
        raw = _api(f"/metrics-service/metrics/maxmet/daily/{s}/{e}")
        for entry in raw or []:
            generic = entry.get("generic") or {}
            v = generic.get("vo2MaxValue")
            d = generic.get("calendarDate")
            if v and d:
                result[d] = v
    except Exception:
        pass
    _cset(key, result, ttl)
    return result


def activities_range(start, end, group="running"):
    s, e = _fmt(start), _fmt(end)
    key = f"acts:{s}:{e}:{group}"
    ttl = LONG_TTL if e < str(date.today()) else SHORT_TTL
    c = _cget(key, ttl)
    if c is not None:
        return c
    type_keys = ACTIVITY_GROUPS.get(group, {group})
    raw = _raw_activities_range(start, end)
    filtered = [a for a in raw if (a.get("activityType") or {}).get("typeKey") in type_keys]
    result = [_fmt_act(a) for a in filtered]
    vo2_map = _vo2max_map(start, end)
    for r in result:
        if r["date"] in vo2_map:
            r["vo2max"] = vo2_map[r["date"]]
    _cset(key, result, ttl)
    return result


def recent_activities(n=10):
    end = date.today()
    return activities_range(end - timedelta(days=120), end)[:n]


def weekly_volume(n=12):
    key = f"wvol:{n}"
    c = _cget(key, SHORT_TTL)
    if c is not None:
        return c
    end = date.today()
    acts = activities_range(end - timedelta(weeks=n), end)
    buckets: dict = {}
    for a in acts:
        try:
            d = datetime.strptime(a["date"], "%Y-%m-%d").date()
            wk = _fmt(d - timedelta(days=d.weekday()))
        except Exception:
            continue
        if wk not in buckets:
            buckets[wk] = {"week": wk, "km": 0.0, "sessions": 0, "elevation": 0}
        buckets[wk]["km"] += a["distance_km"]
        buckets[wk]["sessions"] += 1
        buckets[wk]["elevation"] += a["elevation"]
    result = sorted(buckets.values(), key=lambda x: x["week"])
    for w in result:
        w["km"] = round(w["km"], 1)
    _cset(key, result, SHORT_TTL)
    return result


# ─────────────── HR zones ───────────────

def max_hr_from_activities():
    c = _cget("max_hr", LONG_TTL)
    if c is not None:
        return c
    acts = activities_range(date.today() - timedelta(days=365), date.today())
    mx = max((a["max_hr"] for a in acts if a.get("max_hr")), default=190)
    _cset("max_hr", mx, LONG_TTL)
    return mx


def hr_zones():
    c = _cget("hr_zones", LONG_TTL)
    if c is not None:
        return c
    max_hr = max_hr_from_activities()
    rhr = daily_summary().get("resting_hr") or 48
    hrr = max_hr - rhr
    defs = [
        (1, "Recuperación",  0.50, 0.60, "#4ade80"),
        (2, "Aeróbico base", 0.60, 0.70, "#60a5fa"),
        (3, "Tempo",         0.70, 0.80, "#facc15"),
        (4, "Umbral",        0.80, 0.90, "#fb923c"),
        (5, "Máximo",        0.90, 1.00, "#f87171"),
    ]
    result = [
        {"zone": n, "name": nm, "min_bpm": rhr + round(hrr * lo), "max_bpm": rhr + round(hrr * hi), "color": col}
        for n, nm, lo, hi, col in defs
    ]
    _cset("hr_zones", result, LONG_TTL)
    return result


def zone_time_recent(days=30):
    key = f"zt:{days}"
    c = _cget(key, SHORT_TTL)
    if c is not None:
        return c
    acts = activities_range(date.today() - timedelta(days=days), date.today())
    totals = {i: 0.0 for i in range(1, 6)}
    for a in acts:
        for i in range(1, 6):
            totals[i] += a.get(f"hr_z{i}") or 0
    result = [{"zone": i, "seconds": round(totals[i]), "minutes": round(totals[i] / 60, 1)} for i in range(1, 6)]
    _cset(key, result, SHORT_TTL)
    return result


# ─────────────── VO2max / yearly ───────────────

def vo2max_history(days=90):
    """Usa el VO2max 'genérico' oficial de Garmin (mismo que muestra el reloj/app),
    no el valor puntual guardado en cada actividad, que puede quedarse desfasado."""
    key = f"vo2:{days}"
    c = _cget(key, SHORT_TTL)
    if c is not None:
        return c
    end = date.today()
    start = end - timedelta(days=days)
    vo2_map = _vo2max_map(start, end)
    result = [{"date": d, "vo2max": v} for d, v in sorted(vo2_map.items())]
    _cset(key, result, SHORT_TTL)
    return result


def yearly_stats():
    c = _cget("yearly", SHORT_TTL)
    if c is not None:
        return c
    today = date.today()
    acts = activities_range(date(today.year, 1, 1), today)
    weekly = weekly_volume(52)
    result = {
        "total_km": round(sum(a["distance_km"] for a in acts), 1),
        "total_sessions": len(acts),
        "max_week_km": round(max((w["km"] for w in weekly), default=0), 1),
        "avg_week_km": round(sum(w["km"] for w in weekly) / len(weekly), 1) if weekly else 0,
        "total_elevation": sum(a["elevation"] for a in acts),
    }
    _cset("yearly", result, SHORT_TTL)
    return result


# ─────────────── single-activity detail ───────────────

def activity_summary(activity_id):
    key = f"act_summary:{activity_id}"
    c = _cget(key, LONG_TTL)
    if c is not None:
        return c
    try:
        raw = _api(f"/activity-service/activity/{activity_id}")
        s = raw.get("summaryDTO") or {}
        result = {
            "id": activity_id,
            "name": raw.get("activityName", "Actividad"),
            "date": (s.get("startTimeLocal") or "")[:10],
            "datetime": s.get("startTimeLocal", ""),
            "distance_km": round((s.get("distance", 0) or 0) / 1000, 2),
            "duration": _hms(s.get("duration")),
            "duration_sec": s.get("duration", 0),
            "pace": _pace(s.get("averageSpeed")),
            "avg_hr": _int(s.get("averageHR")),
            "max_hr": _int(s.get("maxHR")),
            "min_hr": _int(s.get("minHR")),
            "calories": s.get("calories"),
            "elevation_gain": round(s.get("elevationGain") or 0),
            "elevation_loss": round(s.get("elevationLoss") or 0),
            "cadence": round(s.get("averageRunCadence") or 0),
            "max_cadence": round(s.get("maxRunCadence") or 0),
            "aerobic_te": _round1(s.get("trainingEffect")),
            "anaerobic_te": _round1(s.get("anaerobicTrainingEffect")),
            "training_label": (s.get("trainingEffectLabel") or "").replace("_", " ").title(),
            "steps": s.get("steps"),
            "avg_power": s.get("averagePower"),
        }
    except Exception:
        result = None
    _cset(key, result, LONG_TTL)
    return result


def activity_splits(activity_id):
    key = f"act_splits:{activity_id}"
    c = _cget(key, LONG_TTL)
    if c is not None:
        return c
    try:
        raw = _api(f"/activity-service/activity/{activity_id}/splits")
        laps = raw.get("lapDTOs") or []
        result = [{
            "n": i,
            "distance_km": round((lap.get("distance", 0) or 0) / 1000, 2),
            "duration": _hms(lap.get("duration")),
            "pace": _pace(lap.get("averageSpeed")),
            "avg_hr": _int(lap.get("averageHR")),
            "max_hr": _int(lap.get("maxHR")),
            "cadence": round(lap.get("averageRunCadence") or 0),
            "elev_gain": round(lap.get("elevationGain") or 0),
            "intensity": (lap.get("intensityType") or "").title(),
        } for i, lap in enumerate(laps, start=1)]
    except Exception:
        result = []
    _cset(key, result, LONG_TTL)
    return result


def activity_series(activity_id):
    """Time-series (HR / pace / cadence / elevation) for the activity-detail charts.
    Asks Garmin to pre-resample to ~200 points server-side (maxChartSize) so no
    manual bucketing is needed here."""
    key = f"act_series:{activity_id}"
    c = _cget(key, LONG_TTL)
    if c is not None:
        return c
    try:
        raw = _api(f"/activity-service/activity/{activity_id}/details",
                    {"maxChartSize": 200, "maxPolylineSize": 0})
        descs = raw.get("metricDescriptors") or []
        idx = {d["key"]: d["metricsIndex"] for d in descs}

        def val(m, k):
            i = idx.get(k)
            return m[i] if i is not None and i < len(m) else None

        result = []
        for row in raw.get("activityDetailMetrics") or []:
            m = row.get("metrics") or []
            speed = val(m, "directSpeed")
            result.append({
                "t": val(m, "sumElapsedDuration"),
                "distance_km": round((val(m, "sumDistance") or 0) / 1000, 3),
                "hr": val(m, "directHeartRate"),
                "cadence": val(m, "directDoubleCadence"),
                "pace_s_km": round(1000 / speed) if speed else None,
                "elevation": val(m, "directElevation"),
            })
    except Exception:
        result = []
    _cset(key, result, LONG_TTL)
    return result


# ─────────────── PRs / race predictions ───────────────

def race_predictions():
    """Garmin's own fitness-based estimate for 5K/10K/half/marathon race times."""
    c = _cget("race_pred", MED_TTL)
    if c is not None:
        return c
    seconds = {"5k": None, "10k": None, "half": None, "marathon": None}
    try:
        raw = _api(f"/metrics-service/metrics/racepredictions/latest/{_dn()}")
        seconds = {
            "5k": raw.get("time5K"),
            "10k": raw.get("time10K"),
            "half": raw.get("timeHalfMarathon"),
            "marathon": raw.get("timeMarathon"),
        }
    except Exception:
        pass
    result = {
        "seconds": seconds,
        "fmt": {k: (_hms(v) if v else None) for k, v in seconds.items()},
    }
    _cset("race_pred", result, MED_TTL)
    return result


def personal_records():
    """Real PRs for 5K/10K/half marathon/marathon (typeId confirmed against this
    account: 3=5K, 4=10K, 5=half marathon, 6=marathon)."""
    c = _cget("personal_records", MED_TTL)
    if c is not None:
        return c
    result = {v: None for v in PR_TYPE_MAP.values()}
    try:
        raw = _api(f"/personalrecord-service/personalrecord/prs/{_dn()}")
        for r in raw or []:
            dist = PR_TYPE_MAP.get(r.get("typeId"))
            if not dist:
                continue
            secs = r.get("value")
            date_str = (r.get("activityStartDateTimeLocalFormatted")
                        or r.get("actStartDateTimeInGMTFormatted") or "")[:10]
            result[dist] = {
                "time": round(secs) if secs else None,
                "time_fmt": _hms(secs) if secs else None,
                "date": date_str,
                "activity_id": r.get("activityId"),
                "activity_name": r.get("activityName"),
            }
    except Exception:
        pass
    _cset("personal_records", result, MED_TTL)
    return result
