"""Analytics and Check Intelligence API. Track I (docs/TRACK_I.md).

Registered once in app/main.py during phase 0. Empty until Track I's phase 2.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/analytics", tags=["console: analytics"])
