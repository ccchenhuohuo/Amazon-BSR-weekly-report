#!/usr/bin/env python3

import re
from datetime import date, timedelta

DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')

MONDAY = 0
TUESDAY = 1
WEDNESDAY = 2
THURSDAY = 3
FRIDAY = 4
SATURDAY = 5
SUNDAY = 6

MYSQL_WEEKDAY_MAP = {
    MONDAY: 2,
    TUESDAY: 3,
    WEDNESDAY: 4,
    THURSDAY: 5,
    FRIDAY: 6,
    SATURDAY: 7,
    SUNDAY: 1
}

WEEKDAY_NAMES = {
    MONDAY: 'Monday',
    TUESDAY: 'Tuesday',
    WEDNESDAY: 'Wednesday',
    THURSDAY: 'Thursday',
    FRIDAY: 'Friday',
    SATURDAY: 'Saturday',
    SUNDAY: 'Sunday'
}

_WEEKDAY_NAME_TO_NUM = {}
for num, name in WEEKDAY_NAMES.items():
    _WEEKDAY_NAME_TO_NUM[name] = num
    _WEEKDAY_NAME_TO_NUM[name.lower()] = num
    _WEEKDAY_NAME_TO_NUM[name.lower()[:3]] = num
for i in range(7):
    _WEEKDAY_NAME_TO_NUM[str(i)] = i

# 添加中文星期支持（简化版：仅支持"周一"到"周日"）
CHINESE_WEEKDAYS = {
    MONDAY: '周一',
    TUESDAY: '周二',
    WEDNESDAY: '周三',
    THURSDAY: '周四',
    FRIDAY: '周五',
    SATURDAY: '周六',
    SUNDAY: '周日'
}
for num, name in CHINESE_WEEKDAYS.items():
    _WEEKDAY_NAME_TO_NUM[name] = num


def parse_weekday_param(weekday_str):
    if isinstance(weekday_str, int):
        if 0 <= weekday_str <= 6:
            return weekday_str
        raise ValueError('Weekday must be 0-6')
    s = str(weekday_str).strip().lower()
    if s in _WEEKDAY_NAME_TO_NUM:
        return _WEEKDAY_NAME_TO_NUM[s]
    raise ValueError('Invalid weekday: %s' % weekday_str)


def validate_date(date_str):
    if not date_str or not DATE_PATTERN.match(date_str):
        return False
    try:
        date.fromisoformat(date_str)
        return True
    except ValueError:
        return False


def parse_date(date_str):
    if not date_str or not DATE_PATTERN.match(date_str):
        return None
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        return None


def is_weekday(date_str, target_weekday):
    d = parse_date(date_str)
    if d is None:
        return False
    return d.weekday() == target_weekday


def get_last_weekday(target_weekday):
    today = date.today()
    days_since = (today.weekday() - target_weekday) % 7
    if days_since == 0:
        days_since = 7
    last = today - timedelta(days=days_since)
    return last.isoformat()


def get_weekday_of_week(d, target_weekday):
    days_to = (target_weekday - d.weekday()) % 7
    return d + timedelta(days=days_to)


def list_weekdays(start_date, target_weekday, end_date=None):
    start = parse_date(start_date)
    if start is None:
        return []
    if end_date is None:
        end = date.today()
    else:
        end = parse_date(end_date)
        if end is None:
            return []
    if start > end:
        return []
    first = get_weekday_of_week(start, target_weekday)
    if first < start:
        first += timedelta(days=7)
    weekdays = []
    current = first
    while current <= end:
        weekdays.append(current.isoformat())
        current += timedelta(days=7)
    return weekdays


def get_all_weekdays_since(target_weekday, start_date='2026-01-01'):
    return list_weekdays(start_date, target_weekday)


def is_friday(date_str):
    return is_weekday(date_str, FRIDAY)


def get_last_friday():
    return get_last_weekday(FRIDAY)


def get_friday_of_week(d):
    return get_weekday_of_week(d, FRIDAY)


def list_fridays(start_date, end_date=None):
    return list_weekdays(start_date, FRIDAY, end_date)


def get_all_fridays_since(start_date='2026-01-01'):
    return get_all_weekdays_since(FRIDAY, start_date)


__all__ = [
    'validate_date', 'parse_date',
    'is_weekday', 'get_last_weekday', 'get_weekday_of_week',
    'list_weekdays', 'get_all_weekdays_since', 'parse_weekday_param',
    'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY', 'SUNDAY',
    'MYSQL_WEEKDAY_MAP', 'WEEKDAY_NAMES',
    'is_friday', 'get_last_friday', 'get_friday_of_week',
    'list_fridays', 'get_all_fridays_since',
    'DATE_PATTERN'
]
