"""Verified full-day exchange closures; unavailable years stay conservative.

CN 2026: https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml
HK 2026: https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2025/ce_SEHK_CT_075_2025.pdf
Half-day sessions are trading days. This does not cover stock suspensions or
invent calendars for funds, custom instruments or unverified years/markets.
"""
from datetime import timedelta
from .common import day

_CN_RANGES = [('01-01', '01-03'), ('02-15', '02-23'), ('04-04', '04-06'),
              ('05-01', '05-05'), ('06-19', '06-21'), ('09-25', '09-27'), ('10-01', '10-07')]
_HK_DAYS = ['01-01', '02-17', '02-18', '02-19', '04-03', '04-06', '04-07',
            '05-01', '05-25', '06-19', '07-01', '10-01', '10-19', '12-25']
_CLOSED = {'HK': {day('2026-' + d) for d in _HK_DAYS}, 'CN': set()}
for first, last in _CN_RANGES:
    d, end = day('2026-' + first), day('2026-' + last)
    while d <= end:
        _CLOSED['CN'].add(d); d += timedelta(days=1)


def no_expected_session(market, quote_date, valuation_date):
    """True only when verified closures/weekends explain the ENTIRE quote gap."""
    market = 'CN' if market in ('SH', 'SZ') else market
    start, end = day(quote_date), day(valuation_date)
    if market not in _CLOSED or start.year != 2026 or end.year != 2026 or end < start:
        return False
    d = start + timedelta(days=1)
    while d <= end:
        if d.weekday() < 5 and d not in _CLOSED[market]: return False
        d += timedelta(days=1)
    return True
