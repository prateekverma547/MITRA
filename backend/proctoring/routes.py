"""Proctoring API: events from the candidate's browser. Track I (docs/TRACK_I.md).

Registered once in app/main.py during phase 0. Empty until Track I's phase 1.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/proctoring", tags=["proctoring"])
