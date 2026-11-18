"""Every API route in the app, including those inside included routers.

FastAPI 0.141 keeps an included router as one `_IncludedRouter` entry in
`app.routes` instead of copying its routes in. A sweep that only looks at
`app.routes` therefore never sees `/interviews/...`, `/api/...` or anything
else registered with `include_router`, and passes while checking nothing. This
walks into each included router through its `original_router`.

Routers here are included without an extra prefix or extra dependencies, so a
route's own `path` and `dependant` are the whole story. If that ever changes,
this helper must learn the include context too.
"""

from fastapi.routing import APIRoute


def api_routes(routes) -> list[APIRoute]:
    found: list[APIRoute] = []
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            found.extend(api_routes(inner.routes))
        elif isinstance(route, APIRoute):
            found.append(route)
    return found


def guards_of(route: APIRoute) -> list:
    """Every dependency callable on the route, at any depth."""
    seen, stack = [], list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if dep.call is not None:
            seen.append(dep.call)
        stack.extend(dep.dependencies)
    return seen
