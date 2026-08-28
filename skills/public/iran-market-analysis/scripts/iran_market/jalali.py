"""Jalali (Persian) <-> Gregorian calendar conversion.

TSETMC, IME and CODAL all return dates as ``dEven`` / ``dEvenMiladi`` style
integers in the **Jalali** calendar (``14030415`` == 1403-04-15).  Converting
them wrongly shifts every bar by ~621 years or by a whole month, which silently
ruins resampling to weekly/monthly and every session-anchored VWAP - so the
conversion lives here, ported from the well-tested ``jalaali-js`` algorithm, with
no third-party dependency.

Reference points pinned by ``tests/skills/iran_market/test_jalali.py``:
1400-01-01 == 2021-03-21, 1403-01-01 == 2024-03-20, 1404-01-01 == 2025-03-21.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

JALALI_MONTHS = (
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
)

JALALI_WEEKDAYS = ("شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه")

_BREAKS = (-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178)


def _div(a: int, b: int) -> int:
    """Truncating integer division (``~~(a / b)`` in jalaali-js).

    Deliberately *not* ``a // b``: Python floors toward ``-inf`` while the
    reference algorithm truncates toward zero, and ``div(gm - 8, 6)`` is negative
    for January-July.  Using floor division shifts every converted date by a
    full year.
    """
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _mod(a: int, b: int) -> int:
    """Truncating modulo (sign follows the dividend), matching jalaali-js ``mod``."""
    return a - _div(a, b) * b


def _jal_cal(jy: int) -> dict[str, int]:
    """Leap-year bookkeeping for a Jalali year (jalaali-js ``jalCal``)."""
    bl = len(_BREAKS)
    gy = jy + 621
    leap_j = -14
    jp = _BREAKS[0]
    jump = 0
    if jy < jp or jy >= _BREAKS[bl - 1]:
        raise ValueError(f"Jalali year out of supported range: {jy}")
    for i in range(1, bl):
        jm = _BREAKS[i]
        jump = jm - jp
        if jy < jm:
            break
        leap_j = leap_j + _div(jump, 33) * 8 + _div(_mod(jump, 33), 4)
        jp = jm
    n = jy - jp
    leap_j = leap_j + _div(n, 33) * 8 + _div(_mod(n, 33) + 3, 4)
    if _mod(jump, 33) == 4 and jump - n == 4:
        leap_j += 1
    leap_g = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150
    march = 20 + leap_j - leap_g
    if jump - n < 6:
        n = n - jump + _div(jump + 4, 33) * 33
    leap = _mod(_mod(n + 1, 33) - 1, 4)
    if leap == -1:
        leap = 4
    return {"leap": leap, "gy": gy, "march": march}


def _g2d(gy: int, gm: int, gd: int) -> int:
    """Gregorian date -> Julian day number."""
    d = (
        _div((gy + _div(gm - 8, 6) + 100100) * 1461, 4)
        + _div(153 * _mod(gm + 9, 12) + 2, 5)
        + gd
        - 34840408
    )
    d = d - _div(_div(gy + 100100 + _div(gm - 8, 6), 100) * 3, 4) + 752
    return d


def _d2g(jdn: int) -> tuple[int, int, int]:
    """Julian day number -> Gregorian date."""
    j = 4 * jdn + 139361631
    j = j + _div(_div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908
    i = _div(_mod(j, 1461), 4) * 5 + 308
    gd = _div(_mod(i, 153), 5) + 1
    gm = _mod(_div(i, 153), 12) + 1
    gy = _div(j, 1461) - 100100 + _div(8 - gm, 6)
    return gy, gm, gd


def _j2d(jy: int, jm: int, jd: int) -> int:
    r = _jal_cal(jy)
    return _g2d(r["gy"], 3, r["march"]) + (jm - 1) * 31 - _div(jm, 7) * (jm - 7) + jd - 1


def _d2j(jdn: int) -> tuple[int, int, int]:
    gy = _d2g(jdn)[0]
    jy = gy - 621
    r = _jal_cal(jy)
    jdn1f = _g2d(gy, 3, r["march"])
    k = jdn - jdn1f
    if k >= 0:
        if k <= 185:
            return jy, 1 + _div(k, 31), _mod(k, 31) + 1
        k -= 186
    else:
        jy -= 1
        k += 179
        if r["leap"] == 1:
            k += 1
    return jy, 7 + _div(k, 30), _mod(k, 30) + 1


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> date:
    gy, gm, gd = _d2g(_j2d(jy, jm, jd))
    return date(gy, gm, gd)


def gregorian_to_jalali(d: date) -> tuple[int, int, int]:
    return _d2j(_g2d(d.year, d.month, d.day))


def is_jalali_leap(jy: int) -> bool:
    return _jal_cal(jy)["leap"] == 0


def jalali_month_length(jy: int, jm: int) -> int:
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    return 30 if is_jalali_leap(jy) else 29


def parse_deven(value: object) -> date | None:
    """Parse a TSETMC ``dEven`` value into a Gregorian :class:`datetime.date`.

    Accepts ``14030415`` (int or str), ``"1403-04-15"`` and already-Gregorian
    ISO strings.  TSETMC feeds occasionally return ``0``/``None`` for suspended
    sessions; those become ``None`` so the caller can drop the bar.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"0", "-1"}:
        return None
    digits = text.replace("-", "").replace("/", "")
    if not digits.isdigit() or len(digits) != 8:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    jy = int(digits[:4])
    jm = int(digits[4:6])
    jd = int(digits[6:8])
    if not (1 <= jm <= 12 and 1 <= jd <= 31):
        return None
    # 1900-2000 cannot be a plausible Jalali year for market data, but a feed
    # that already converted to Gregorian would produce them.
    if jy < 1300:
        try:
            return date(jy, jm, jd)
        except ValueError:
            return None
    try:
        return jalali_to_gregorian(jy, jm, jd)
    except ValueError:
        return None


def format_jalali(d: date | datetime | None) -> str:
    if d is None:
        return ""
    jy, jm, jd = gregorian_to_jalali(d.date() if isinstance(d, datetime) else d)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def format_jalali_long(d: date | datetime | None) -> str:
    if d is None:
        return ""
    day = d.date() if isinstance(d, datetime) else d
    jy, jm, jd = gregorian_to_jalali(day)
    return f"{jd} {JALALI_MONTHS[jm - 1]} {jy}"


def jalali_weekday(d: date | datetime) -> str:
    day = d.date() if isinstance(d, datetime) else d
    # Monday == 0 in Python; Saturday is the first day of the Iranian week.
    return JALALI_WEEKDAYS[(day.weekday() + 2) % 7]


def is_tse_trading_day(d: date) -> bool:
    """Approximation of the TSE calendar: Friday is the only weekly holiday.

    Official Iranian holidays are *not* modelled here (they need the yearly
    calendar); live data simply has no bar for those days, and the synthetic
    generator uses this filter to stay realistic.
    """
    return d.weekday() != 4  # 4 == Friday


def previous_trading_day(d: date) -> date:
    current = d - timedelta(days=1)
    while not is_tse_trading_day(current):
        current -= timedelta(days=1)
    return current


def trading_days_back(end: date, count: int) -> list[date]:
    """``count`` consecutive trading days ending at (and including) ``end``."""
    days: list[date] = []
    current = end
    while len(days) < count:
        if is_tse_trading_day(current):
            days.append(current)
        current -= timedelta(days=1)
    return list(reversed(days))


def jalali_now(now: datetime | None = None) -> str:
    return format_jalali(now or datetime.now())


__all__ = [
    "JALALI_MONTHS",
    "JALALI_WEEKDAYS",
    "format_jalali",
    "format_jalali_long",
    "gregorian_to_jalali",
    "is_jalali_leap",
    "is_tse_trading_day",
    "jalali_month_length",
    "jalali_now",
    "jalali_to_gregorian",
    "jalali_weekday",
    "parse_deven",
    "previous_trading_day",
    "trading_days_back",
]
