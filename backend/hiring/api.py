"""The hiring package's one router.

`app/main.py` registers `hiring.routes.router` and nothing else from this
package. Each module of routes (candidate moves, job requests, and screening
and the HR round later) decorates this same object, and `hiring/routes.py`
imports each of them so their routes exist. Adding a route module means one
import line there, never an edit to main.py.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["console: hiring"])
