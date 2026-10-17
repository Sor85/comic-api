"""Five-field cron schedules (minute hour day-of-month month day-of-week) in the server's local time.

Follows Vixie cron: when both day fields are restricted a day matches either one; day-of-week 0 and 7 are Sunday.
"""
import re
from datetime import datetime, timedelta

FIELDS = (("分钟", 0, 59), ("小时", 0, 23), ("日期", 1, 31), ("月份", 1, 12), ("星期", 0, 7))
NAMES = {3: ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
         4: ("sun", "mon", "tue", "wed", "thu", "fri", "sat")}
WEEKDAYS = "日一二三四五六"
MIN_GAP_MINUTES = 5
HORIZON = timedelta(days=366 * 5)
DEFAULT = "0 * * * *"


class CronError(ValueError):
    pass


def _value(text, index):
    label, low, high = FIELDS[index]
    names = NAMES.get(index, ())
    if text in names:
        return names.index(text) + (1 if index == 3 else 0)
    if not text.isdigit() or not low <= int(text) <= high:
        raise CronError(f"{label}字段的取值“{text}”超出 {low}–{high}")
    return int(text)


def _field(text, index):
    label, low, high = FIELDS[index]
    values = set()
    for part in text.split(","):
        match = re.fullmatch(r"(\*|[0-9a-z]+(?:-[0-9a-z]+)?)(?:/([0-9]+))?", part)
        if not match:
            raise CronError(f"{label}字段“{text}”格式无效")
        base, step = match.group(1), int(match.group(2) or 1)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            start, end = (_value(value, index) for value in base.split("-"))
        else:
            start = _value(base, index)
            end = high if match.group(2) else start
        if step < 1 or start > end:
            raise CronError(f"{label}字段“{text}”的范围或步长无效")
        values.update(range(start, end + 1, step))
    return {value % 7 for value in values} if index == 4 else values


def parse(expression):
    if not isinstance(expression, str):
        raise CronError("Cron 表达式必须是文本")
    parts = expression.strip().lower().split()
    if len(parts) != 5:
        raise CronError("Cron 表达式需要 5 个字段：分钟 小时 日期 月份 星期")
    fields = [_field(part, index) for index, part in enumerate(parts)]
    return fields, parts[2].startswith("*"), parts[4].startswith("*")


def _day_matches(moment, days, weekdays, any_day, any_weekday):
    by_day, by_weekday = moment.day in days, (moment.weekday() + 1) % 7 in weekdays
    if any_day or any_weekday:
        return by_day and by_weekday
    return by_day or by_weekday


def next_after(expression, after):
    """The first matching minute strictly after ``after`` (a naive local datetime)."""
    (minutes, hours, days, months, weekdays), any_day, any_weekday = parse(expression)
    moment = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = moment + HORIZON
    while moment < limit:
        if moment.month not in months:
            moment = (moment.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
        elif not _day_matches(moment, days, weekdays, any_day, any_weekday):
            moment = moment.replace(hour=0, minute=0) + timedelta(days=1)
        elif moment.hour not in hours:
            moment = moment.replace(minute=0) + timedelta(hours=1)
        elif moment.minute not in minutes:
            moment += timedelta(minutes=1)
        else:
            return moment
    raise CronError("Cron 表达式在 5 年内不会运行")


def upcoming(expression, count, after=None, within=None):
    """Up to ``count`` runs; with ``within`` (a timedelta) the scan stops past that window after the first run."""
    moment, runs = after or datetime.now(), []
    for _ in range(count):
        try:
            moment = next_after(expression, moment)
        except CronError:
            if runs:
                break
            raise
        if runs and within and moment - runs[0] > within:
            break
        runs.append(moment)
    return runs


def validate(expression):
    """Normalized expression; rejects schedules that never run or run less than five minutes apart."""
    parse(expression)
    normalized = " ".join(expression.strip().lower().split())
    runs = upcoming(normalized, 120, within=timedelta(days=62))
    if any(later - earlier < timedelta(minutes=MIN_GAP_MINUTES) for earlier, later in zip(runs, runs[1:])):
        raise CronError(f"相邻两次同步至少间隔 {MIN_GAP_MINUTES} 分钟")
    return normalized


def from_interval(minutes):
    """Closest schedule for a legacy fixed interval in minutes."""
    minutes = int(minutes) if isinstance(minutes, int) and minutes > 0 else 60
    if minutes < 60:
        return f"*/{max(MIN_GAP_MINUTES, minutes)} * * * *"
    if minutes < 1440:
        hours = round(minutes / 60)
        return DEFAULT if hours == 1 else f"0 */{hours} * * *"
    if minutes < 10080:
        days = round(minutes / 1440)
        return "0 0 * * *" if days == 1 else f"0 0 */{days} * *"
    return "0 0 * * 0"


def _weekdays(text):
    days = _field(text, 4)
    if days == {1, 2, 3, 4, 5}:
        return "每个工作日"
    if days == {0, 6}:
        return "每个周末"
    return "每周" + "、".join(WEEKDAYS[day] for day in sorted(days, key=lambda day: (day == 0, day)))


def describe(expression):
    """Chinese summary of common schedules; anything else falls back to the expression itself."""
    try:
        parse(expression)
    except CronError:
        return expression
    minute, hour, day, month, weekday = expression.strip().lower().split()
    clock = f"{int(hour):02d}:{int(minute):02d}" if minute.isdigit() and hour.isdigit() else ""
    if month == "*":
        if day == weekday == "*":
            if hour == "*" and (step := re.fullmatch(r"\*/(\d+)", minute)):
                return f"每 {step.group(1)} 分钟"
            if minute.isdigit() and (hour == "*" or (step := re.fullmatch(r"\*/(\d+)", hour))):
                every = "每小时" if hour == "*" else f"每 {step.group(1)} 小时"
                return f"{every}整点" if minute == "0" else f"{every}的第 {int(minute)} 分"
            if clock:
                return f"每天 {clock}"
        if clock and day == "*" and weekday != "*":
            return f"{_weekdays(weekday)} {clock}"
        if clock and weekday == "*" and day.isdigit():
            return f"每月 {int(day)} 日 {clock}"
    return f"自定义：{' '.join(expression.split())}"


def timezone_label():
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    return f"{now.tzname()}（UTC{offset[:3]}:{offset[3:]}）"


def preview(expression, count=5):
    """Validated expression, its summary and the next runs, labelled in the server's local time."""
    normalized = validate(expression)
    runs = upcoming(normalized, count)
    return {"expression": normalized, "description": describe(normalized), "timezone": timezone_label(),
            "runs": [{"at": run.timestamp(), "label": f"{run:%Y-%m-%d} 周{WEEKDAYS[(run.weekday() + 1) % 7]} {run:%H:%M}"}
                     for run in runs]}
