"""Warehouse logbook helpers for daily archive reports."""
import calendar
from datetime import date, datetime, timedelta
from pathlib import Path

from archiver.archive_service import ArchiveService
from shared.config_manager import (
    atomic_save_json,
    load_mnemo_tree,
    load_runtime_json,
    load_system_config,
)


from shared.paths import app_root as _app_root

ROOT_DIR = Path(_app_root())
DATA_DIR = ROOT_DIR / "data"
CONFIG_DIR = DATA_DIR / "config"
REPORTS_CONFIG_PATH = CONFIG_DIR / "reports_config.json"
OPERATORS_PATH = CONFIG_DIR / "operators.json"
HOLIDAYS_PATH = CONFIG_DIR / "holidays.json"
SIGNOFFS_PATH = DATA_DIR / "logbook_signoffs.json"

DEFAULT_COLUMNS = ["t_min", "t_max", "t_avg", "h_min", "h_max", "h_avg", "violations"]


class LogbookError(ValueError):
    pass


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _parse_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise LogbookError("Неверный формат даты, нужен YYYY-MM-DD")


def _date_range(start, end):
    current = _parse_date(start)
    last = _parse_date(end)
    if current > last:
        raise LogbookError("Дата начала больше даты окончания")
    while current <= last:
        yield current
        current += timedelta(days=1)


def _unique_ints(values):
    result = []
    seen = set()
    for value in values or []:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _unique_strings(values):
    result = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _all_enabled_sensor_ids():
    return [
        int(sensor["id"])
        for sensor in load_system_config().get("sensors", [])
        if sensor.get("id") is not None and sensor.get("enabled", True)
    ]


def _default_reports_config():
    return {
        "reports": [
            {
                "id": "rep1",
                "name": "Складской журнал",
                "type": "warehouse",
                "sensor_ids": _all_enabled_sensor_ids(),
                "branch_ids": [],
                "columns": list(DEFAULT_COLUMNS),
                "require_daily_signoff": True,
                "active": True,
            }
        ],
        "updated_at": _now_iso(),
    }


def _default_operators():
    return {
        "operators": [
            {
                "id": "op1",
                "last_name": "Ответственный",
                "first_name": "Сотрудник",
                "middle_name": "",
                "position": "оператор",
                "active": True,
            }
        ],
        "updated_at": _now_iso(),
    }


def _default_holidays():
    return {
        "weekends": [5, 6],
        "holidays": [],
        "rf_calendar_years": [],
        "updated_at": _now_iso(),
    }


def _default_signoffs():
    return {"signoffs": [], "updated_at": _now_iso()}


def load_reports_config():
    payload = load_runtime_json(str(REPORTS_CONFIG_PATH), default={})
    if not isinstance(payload, dict) or not isinstance(payload.get("reports"), list):
        payload = _default_reports_config()
        atomic_save_json(str(REPORTS_CONFIG_PATH), payload)
    return normalize_reports_config(payload)


def save_reports_config(payload):
    normalized = normalize_reports_config(payload or {})
    normalized["updated_at"] = _now_iso()
    atomic_save_json(str(REPORTS_CONFIG_PATH), normalized)
    return normalized


def normalize_reports_config(payload):
    reports = []
    seen_ids = set()
    for index, item in enumerate(payload.get("reports") or [], start=1):
        if not isinstance(item, dict):
            continue
        report_id = str(item.get("id") or f"rep{index}").strip()
        if not report_id or report_id in seen_ids:
            report_id = _next_id("rep", seen_ids)
        seen_ids.add(report_id)
        report_type = item.get("type") if item.get("type") in ("warehouse", "general") else "warehouse"
        columns = [col for col in (item.get("columns") or DEFAULT_COLUMNS) if col in DEFAULT_COLUMNS]
        if not columns:
            columns = list(DEFAULT_COLUMNS)
        reports.append({
            "id": report_id,
            "name": str(item.get("name") or f"Журнал {index}").strip()[:120],
            "type": report_type,
            "sensor_ids": _unique_ints(item.get("sensor_ids")),
            "branch_ids": _unique_strings(item.get("branch_ids")),
            "columns": columns,
            "require_daily_signoff": bool(item.get("require_daily_signoff", report_type == "warehouse")),
            "active": bool(item.get("active", True)),
        })
    if not reports:
        reports = _default_reports_config()["reports"]
    return {"reports": reports, "updated_at": payload.get("updated_at") or _now_iso()}


def _next_id(prefix, existing):
    index = 1
    while f"{prefix}{index}" in existing:
        index += 1
    return f"{prefix}{index}"


def load_operators():
    payload = load_runtime_json(str(OPERATORS_PATH), default={})
    if not isinstance(payload, dict) or not isinstance(payload.get("operators"), list):
        payload = _default_operators()
        atomic_save_json(str(OPERATORS_PATH), payload)
    return normalize_operators(payload)


def save_operators(payload):
    normalized = normalize_operators(payload or {})
    normalized["updated_at"] = _now_iso()
    atomic_save_json(str(OPERATORS_PATH), normalized)
    return normalized


def normalize_operators(payload):
    operators = []
    seen_ids = set()
    for index, item in enumerate(payload.get("operators") or [], start=1):
        if not isinstance(item, dict):
            continue
        operator_id = str(item.get("id") or f"op{index}").strip()
        if not operator_id or operator_id in seen_ids:
            operator_id = _next_id("op", seen_ids)
        seen_ids.add(operator_id)
        operators.append({
            "id": operator_id,
            "last_name": str(item.get("last_name") or "").strip()[:80],
            "first_name": str(item.get("first_name") or "").strip()[:80],
            "middle_name": str(item.get("middle_name") or "").strip()[:80],
            "position": str(item.get("position") or "").strip()[:80],
            "active": bool(item.get("active", True)),
        })
    if not operators:
        operators = _default_operators()["operators"]
    return {"operators": operators, "updated_at": payload.get("updated_at") or _now_iso()}


def load_holidays():
    payload = load_runtime_json(str(HOLIDAYS_PATH), default={})
    if not isinstance(payload, dict):
        payload = _default_holidays()
        atomic_save_json(str(HOLIDAYS_PATH), payload)
    return normalize_holidays(payload)


def save_holidays(payload):
    normalized = normalize_holidays(payload or {})
    normalized["updated_at"] = _now_iso()
    atomic_save_json(str(HOLIDAYS_PATH), normalized)
    return normalized


def normalize_holidays(payload):
    weekends = _unique_ints(payload.get("weekends"))
    weekends = [item for item in weekends if 0 <= item <= 6]
    if not weekends:
        weekends = [5, 6]
    holidays = []
    seen_dates = set()
    for item in payload.get("holidays") or []:
        if not isinstance(item, dict):
            continue
        try:
            holiday_date = _parse_date(item.get("date")).isoformat()
        except LogbookError:
            continue
        if holiday_date in seen_dates:
            continue
        seen_dates.add(holiday_date)
        holidays.append({
            "date": holiday_date,
            "name": str(item.get("name") or "Праздничный день").strip()[:120],
        })
    holidays.sort(key=lambda item: item["date"])
    return {
        "weekends": weekends,
        "holidays": holidays,
        "rf_calendar_years": _unique_ints(payload.get("rf_calendar_years")),
        "updated_at": payload.get("updated_at") or _now_iso(),
    }


def load_rf_calendar(year):
    try:
        year = int(year)
    except (TypeError, ValueError):
        raise LogbookError("Год должен быть числом")
    if year < 2000 or year > 2100:
        raise LogbookError("Год вне допустимого диапазона")
    payload = load_holidays()
    fixed = [
        ("01-01", "Новогодние каникулы"),
        ("01-02", "Новогодние каникулы"),
        ("01-03", "Новогодние каникулы"),
        ("01-04", "Новогодние каникулы"),
        ("01-05", "Новогодние каникулы"),
        ("01-06", "Новогодние каникулы"),
        ("01-07", "Рождество Христово"),
        ("01-08", "Новогодние каникулы"),
        ("02-23", "День защитника Отечества"),
        ("03-08", "Международный женский день"),
        ("05-01", "Праздник Весны и Труда"),
        ("05-09", "День Победы"),
        ("06-12", "День России"),
        ("11-04", "День народного единства"),
    ]
    by_date = {item["date"]: item for item in payload.get("holidays") or []}
    for suffix, name in fixed:
        day = f"{year}-{suffix}"
        by_date.setdefault(day, {"date": day, "name": name})
    payload["holidays"] = sorted(by_date.values(), key=lambda item: item["date"])
    years = set(payload.get("rf_calendar_years") or [])
    years.add(year)
    payload["rf_calendar_years"] = sorted(years)
    return save_holidays(payload)


def _load_signoffs():
    payload = load_runtime_json(str(SIGNOFFS_PATH), default={})
    if not isinstance(payload, dict) or not isinstance(payload.get("signoffs"), list):
        payload = _default_signoffs()
        atomic_save_json(str(SIGNOFFS_PATH), payload)
    return payload


def _save_signoffs(payload):
    payload["updated_at"] = _now_iso()
    atomic_save_json(str(SIGNOFFS_PATH), payload)
    return payload


def operator_display_name(operator):
    if not operator:
        return ""
    last = str(operator.get("last_name") or "").strip()
    first = str(operator.get("first_name") or "").strip()
    middle = str(operator.get("middle_name") or "").strip()
    initials = ""
    if first:
        initials += f"{first[0]}."
    if middle:
        initials += f" {middle[0]}."
    return (last + (" " + initials if initials else "")).strip() or first or operator.get("id") or ""


def get_report(report_id):
    for report in load_reports_config().get("reports") or []:
        if report.get("id") == report_id:
            return report
    raise LogbookError("Журнал не найден")


def report_sensor_ids(report):
    ids = list(report.get("sensor_ids") or [])
    branch_ids = set(report.get("branch_ids") or [])
    if branch_ids:
        ids.extend(_sensor_ids_from_branches(load_mnemo_tree().get("branches") or [], branch_ids))
    if not ids:
        ids = _all_enabled_sensor_ids()
    return _unique_ints(ids)


def _sensor_ids_from_branches(branches, branch_ids):
    result = []
    for branch in branches or []:
        if branch.get("id") in branch_ids:
            result.extend(branch.get("sensor_ids") or [])
        result.extend(_sensor_ids_from_branches(branch.get("children") or [], branch_ids))
    return result


def is_working_day(day, holidays=None):
    holidays = holidays or load_holidays()
    holiday_dates = {item.get("date") for item in holidays.get("holidays") or []}
    if day.weekday() in set(holidays.get("weekends") or [5, 6]):
        return False
    return day.isoformat() not in holiday_dates


def daily_rows(report_id, date_from=None, date_to=None):
    today = date.today()
    date_to = _parse_date(date_to) if date_to else today
    date_from = _parse_date(date_from) if date_from else date_to - timedelta(days=13)
    report = get_report(report_id)
    sensors = _sensor_map()
    sensor_ids = report_sensor_ids(report)
    holidays = load_holidays()
    signoffs = _signoff_map()
    archive_rows = ArchiveService().daily_row_map()

    rows = []
    for day in _date_range(date_from, date_to):
        day_text = day.isoformat()
        working = is_working_day(day, holidays)
        signoff = signoffs.get((report_id, day_text))
        snapshot = signoff.get("snapshot") if signoff else None
        sensor_rows = []
        for sid in sensor_ids:
            values = None
            if snapshot:
                values = snapshot.get(str(sid))
            if values is None:
                values = (archive_rows.get(day_text) or {}).get(str(sid))
            sensor_rows.append(_daily_sensor_row(sid, sensors, values))

        requires_signoff = bool(report.get("require_daily_signoff"))
        overdue = bool(requires_signoff and working and not signoff and day < today)
        rows.append({
            "date": day_text,
            "weekday": _weekday_name(day),
            "working_day": working,
            "non_working": not working,
            "is_today": day == today,
            "requires_signoff": requires_signoff,
            "overdue": overdue,
            "signoff": signoff,
            "sensors": sensor_rows,
            "has_data": any(row.get("sample_count") for row in sensor_rows),
        })

    summary = {
        "total_days": len(rows),
        "working_days": sum(1 for row in rows if row["working_day"]),
        "signed_days": sum(1 for row in rows if row.get("signoff")),
        "overdue_days": sum(1 for row in rows if row["overdue"]),
    }
    return {
        "report": report,
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "rows": rows,
        "summary": summary,
    }


def _weekday_name(day):
    names = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    return names[day.weekday()]


def _sensor_map():
    return {int(sensor["id"]): sensor for sensor in load_system_config().get("sensors", []) if sensor.get("id") is not None}


def _daily_sensor_row(sid, sensors, values):
    sensor = sensors.get(int(sid), {})
    values = values or {}
    return {
        "sensor_id": int(sid),
        "sensor_name": values.get("sensor_name") or sensor.get("name") or f"Датчик {sid}",
        "t_min": values.get("t_min"),
        "t_max": values.get("t_max"),
        "t_avg": values.get("t_avg"),
        "h_min": values.get("h_min"),
        "h_max": values.get("h_max"),
        "h_avg": values.get("h_avg"),
        "violations": int(values.get("violations") or 0),
        "sample_count": int(values.get("sample_count") or 0),
    }


def _signoff_map():
    result = {}
    for item in _load_signoffs().get("signoffs") or []:
        key = (item.get("report_id"), item.get("date"))
        if key[0] and key[1]:
            result[key] = item
    return result


def signoff_day(report_id, day, operator_id, comment="", include_previous_non_working=False):
    day = _parse_date(day)
    report = get_report(report_id)
    operator = _operator_by_id(operator_id)
    if not operator:
        raise LogbookError("Оператор не найден")
    payload = _load_signoffs()
    existing = _signoff_map()
    key = (report_id, day.isoformat())
    if key in existing:
        raise LogbookError("Этот день уже подтверждён")

    covered = []
    signed_at = _now_iso()
    operator_name = operator_display_name(operator)
    if include_previous_non_working:
        for non_working_day in _previous_non_working_days(day):
            nw_key = (report_id, non_working_day.isoformat())
            if nw_key in existing:
                continue
            payload["signoffs"].append(_signoff_record(
                report,
                non_working_day,
                operator,
                operator_name,
                signed_at,
                comment,
                non_working=True,
                batch_covered=[],
            ))
            covered.append(non_working_day.isoformat())

    record = _signoff_record(
        report,
        day,
        operator,
        operator_name,
        signed_at,
        comment,
        non_working=not is_working_day(day),
        batch_covered=covered,
    )
    payload["signoffs"].append(record)
    _save_signoffs(payload)
    return {"status": "ok", "signoff": record, "batch_covered": covered}


def _signoff_record(report, day, operator, operator_name, signed_at, comment, non_working, batch_covered):
    return {
        "report_id": report.get("id"),
        "date": day.isoformat(),
        "operator_id": operator.get("id"),
        "operator_name": operator_name,
        "operator_position": operator.get("position") or "",
        "signed_at": signed_at,
        "comment": str(comment or "")[:500],
        "non_working": bool(non_working),
        "batch_covered": list(batch_covered or []),
        "snapshot": _snapshot(report, day),
    }


def _operator_by_id(operator_id):
    for operator in load_operators().get("operators") or []:
        if operator.get("id") == operator_id and operator.get("active", True):
            return operator
    return None


def _snapshot(report, day):
    archive_rows = ArchiveService().daily_row_map()
    sensors = _sensor_map()
    day_values = archive_rows.get(day.isoformat()) or {}
    result = {}
    for sid in report_sensor_ids(report):
        result[str(sid)] = _daily_sensor_row(sid, sensors, day_values.get(str(sid)))
    return result


def _previous_non_working_days(day):
    holidays = load_holidays()
    result = []
    current = day - timedelta(days=1)
    while not is_working_day(current, holidays):
        result.append(current)
        current -= timedelta(days=1)
    result.reverse()
    return result


def month_period(year=None, month=None):
    today = date.today()
    year = int(year or today.year)
    month = int(month or today.month)
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    return first, last


def print_context(report_id, year=None, month=None):
    first, last = month_period(year, month)
    data = daily_rows(report_id, first.isoformat(), last.isoformat())
    return {
        "report": data["report"],
        "rows": data["rows"],
        "summary": data["summary"],
        "period_from": first.isoformat(),
        "period_to": last.isoformat(),
        "month_label": f"{first.year}-{first.month:02d}",
        "system": load_system_config().get("system") or {},
        "generated_at": _now_iso(),
    }
