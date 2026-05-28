from __future__ import annotations

import datetime as dt

MORNING_OPEN = dt.time(9, 0)
MORNING_CLOSE = dt.time(11, 30)
AFTERNOON_OPEN = dt.time(12, 30)
MARKET_CLOSE = dt.time(15, 30)


def nth_weekday(year: int, month: int, weekday: int, nth: int) -> dt.date:
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 7 * (nth - 1))


def vernal_equinox_day(year: int) -> int:
    if 1900 <= year <= 2099:
        return int(20.8431 + 0.242194 * (year - 1980) - ((year - 1980) // 4))
    return 20


def autumnal_equinox_day(year: int) -> int:
    if 1900 <= year <= 2099:
        return int(23.2488 + 0.242194 * (year - 1980) - ((year - 1980) // 4))
    return 23


def national_holidays(year: int) -> set[dt.date]:
    holidays = {
        dt.date(year, 1, 1),
        nth_weekday(year, 1, 0, 2),
        dt.date(year, 2, 11),
        dt.date(year, 2, 23),
        dt.date(year, 3, vernal_equinox_day(year)),
        dt.date(year, 4, 29),
        dt.date(year, 5, 3),
        dt.date(year, 5, 4),
        dt.date(year, 5, 5),
        nth_weekday(year, 7, 0, 3),
        dt.date(year, 8, 11),
        nth_weekday(year, 9, 0, 3),
        dt.date(year, 9, autumnal_equinox_day(year)),
        nth_weekday(year, 10, 0, 2),
        dt.date(year, 11, 3),
        dt.date(year, 11, 23),
    }

    # Substitute holidays. If a holiday falls on Sunday, the next non-holiday
    # weekday becomes a holiday.
    for holiday in sorted(list(holidays)):
        if holiday.weekday() != 6:
            continue
        substitute = holiday + dt.timedelta(days=1)
        while substitute in holidays:
            substitute += dt.timedelta(days=1)
        holidays.add(substitute)

    # Citizen's holiday between two national holidays.
    day = dt.date(year, 1, 2)
    end = dt.date(year, 12, 30)
    while day <= end:
        if day.weekday() < 5 and day not in holidays:
            if day - dt.timedelta(days=1) in holidays and day + dt.timedelta(days=1) in holidays:
                holidays.add(day)
        day += dt.timedelta(days=1)

    return holidays


def is_exchange_holiday(day: dt.date) -> bool:
    if day.weekday() >= 5:
        return True
    if day.month == 1 and day.day in (1, 2, 3):
        return True
    if day.month == 12 and day.day == 31:
        return True
    return day in national_holidays(day.year)


def is_trading_day(day: dt.date) -> bool:
    return not is_exchange_holiday(day)


def is_market_time(ts: dt.datetime) -> bool:
    if not is_trading_day(ts.date()):
        return False
    t = ts.time()
    return (MORNING_OPEN <= t <= MORNING_CLOSE) or (AFTERNOON_OPEN <= t <= MARKET_CLOSE)


def next_trading_day(day: dt.date) -> dt.date:
    cur = day + dt.timedelta(days=1)
    while not is_trading_day(cur):
        cur += dt.timedelta(days=1)
    return cur


def next_market_open(after: dt.datetime) -> dt.datetime:
    if is_trading_day(after.date()):
        if after.time() < MORNING_OPEN:
            return dt.datetime.combine(after.date(), MORNING_OPEN)
        if MORNING_CLOSE < after.time() < AFTERNOON_OPEN:
            return dt.datetime.combine(after.date(), AFTERNOON_OPEN)
    return dt.datetime.combine(next_trading_day(after.date()), MORNING_OPEN)


def market_label(now: dt.datetime) -> str:
    if is_market_time(now):
        return "取引時間中"
    if not is_trading_day(now.date()):
        return "休場日"
    return "市場外"
