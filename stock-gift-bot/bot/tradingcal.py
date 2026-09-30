"""Taiwan stock market trading-day helpers."""
from datetime import date, timedelta


class TradingCalendar:
    def __init__(self, holidays: set[date] | None = None):
        self.holidays = holidays or set()

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def shift(self, d: date, n: int) -> date:
        """Move n trading days from d (negative = backwards). d itself need not be a trading day."""
        step = 1 if n >= 0 else -1
        left = abs(n)
        while left:
            d += timedelta(days=step)
            if self.is_trading_day(d):
                left -= 1
        return d

    def trading_days_between(self, start: date, end: date) -> int:
        """Trading days in (start, end]."""
        count, d = 0, start
        while d < end:
            d += timedelta(days=1)
            if self.is_trading_day(d):
                count += 1
        return count
