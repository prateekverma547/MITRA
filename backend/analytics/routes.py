"""Analytics API, registered once through app/main.py's CONSOLE_PACKAGES.

Track I's summary routes (`/api/analytics/summary/...`) are the whole of it for
now; when more analytics routes arrive they are included here, not in main.py.
"""

from analytics.summary.routes import router

__all__ = ["router"]
