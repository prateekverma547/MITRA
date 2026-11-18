"""Live view API: the event relay and watchers' notes. Track I (docs/TRACK_I.md).

Registered once in app/main.py during phase 0 so that adding a route here never
means editing main.py. Empty until Track I's phase 2.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/live", tags=["console: live"])
