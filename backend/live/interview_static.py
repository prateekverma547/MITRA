"""Serving the candidate app at /interview/: types, caching, compression, and its
security rule (Content-Security-Policy, enforcing since B-89).

**Types.** `.wasm` is `application/wasm` (browsers refuse to stream-compile it
otherwise); MediaPipe's `.task` model files are `application/octet-stream`.

**Caching.**
- `assets/*`: Vite puts a content hash in every name, so a changed file is a
  new URL: `public, max-age=31536000, immutable`.
- `mediapipe/*` (the WebAssembly runtime and the `.task` models) and
  everything else: fixed names, so a long cache would keep a stale model after
  a deploy. `no-cache` with an ETag: the browser keeps the bytes and asks "is
  it still this?" each load, and gets a 304 with no body when it is. Versioned
  paths would avoid even that request, but the names come from the candidate
  app's build (Session B's); recorded as a request (C-66).

**Compression.** Done once at build time (`scripts/precompress.mjs` writes
`.br` and `.gz` beside each compressible file), never per response. A request
gets the brotli file if it accepts `br`, else the gzip file if it accepts
`gzip`, else the original; `Vary: Accept-Encoding` either way. A build with no
precompressed files is served uncompressed and still works.

**The security rule.** Every /interview/ response carries
`Content-Security-Policy-Report-Only` (`POLICY`): violations are reported to
`REPORT_PATH`, nothing is blocked yet. Sources: our own server; what MediaPipe's
WebAssembly needs ('wasm-unsafe-eval', a same-origin module worker); Daily's
hosts for call-object mode with `avoidEval`, from Daily's own guide
(https://docs.daily.co/guides/privacy-and-security/content-security-policy).
Nothing else. Steps to enforce: C_findings.md (C-68).
"""

import hashlib
import mimetypes
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

CSP_HEADER = "Content-Security-Policy"
REPORT_PATH = "/api/live/csp-report"

_DAILY_HTTPS = "https://*.daily.co https://*.dailywebrtc.com https://*.dailywebrtc.net"
_DAILY_WSS = "wss://*.daily.co wss://*.dailywebrtc.com wss://*.dailywebrtc.net"

#: Daily's guide, call-object mode, with `avoidEval: true` (so no 'unsafe-eval':
#: its hosts in script-src instead) and its narrowed wss: list. `blob:` in
#: script-src and worker-src is Daily's ("Call object mode ... worker-src 'self'
#: blob:"); our own app was measured without it (C-67).
DIRECTIVES = {
    "default-src": "'self'",
    "script-src": f"'self' 'wasm-unsafe-eval' {_DAILY_HTTPS} blob:",
    "worker-src": "'self' blob:",
    "connect-src": f"'self' {_DAILY_HTTPS} https://prod-ks.pluot.blue {_DAILY_WSS}",
    "img-src": "'self'",
    # data: measured, not assumed: Vite inlines the smallest font files as data:
    # URLs (C-67); without it the first Chrome run reported font-src blocked=data.
    "font-src": "'self' data:",
    "style-src": "'self'",
    "media-src": "'self'",
    "frame-src": "'self'",
    "object-src": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
    "report-uri": REPORT_PATH,
}
POLICY = "; ".join(f"{k} {v}" for k, v in DIRECTIVES.items())

IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
_TYPES = {".wasm": "application/wasm", ".task": "application/octet-stream",
          ".mjs": "text/javascript", ".js": "text/javascript"}


def interview_headers() -> dict[str, str]:
    """Headers every /interview/ response carries."""
    return {CSP_HEADER: POLICY}


def content_type(path: Path) -> str:
    return _TYPES.get(path.suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _accepts(accept_encoding: str, coding: str) -> bool:
    for part in (accept_encoding or "").lower().split(","):
        name, _, params = part.strip().partition(";")
        if name.strip() == coding:
            return params.replace(" ", "") not in ("q=0", "q=0.0", "q=0.00", "q=0.000")
    return False


def _etag(path: Path) -> str:
    """Per file and per variant: size and modification time, as Starlette does."""
    st = path.stat()
    return '"' + hashlib.md5(f"{st.st_mtime}-{st.st_size}".encode(), usedforsecurity=False).hexdigest() + '"'


def file_response(dist: Path, path: str, accept_encoding: str, if_none_match: str = "") -> Response:
    """A real file under `dist`, with its type, caching and best precompressed variant.

    Answers a matching If-None-Match with 304 and no body: Starlette's
    FileResponse sends an ETag but does not do this itself (measured, C-66), so
    without it "revalidate" would mean "download again".
    """
    target = (dist / path).resolve()
    headers = {**interview_headers(), "Vary": "Accept-Encoding",
               "Cache-Control": IMMUTABLE if path.startswith("assets/") else REVALIDATE}
    served = target
    for coding, suffix in (("br", ".br"), ("gzip", ".gz")):
        variant = target.with_name(target.name + suffix)
        if _accepts(accept_encoding, coding) and variant.is_file():
            served = variant
            headers["Content-Encoding"] = coding
            break
    etag = _etag(served)
    headers["ETag"] = etag
    if etag in {t.strip() for t in (if_none_match or "").split(",")}:
        headers.pop("Content-Encoding", None)
        return Response(status_code=304, headers=headers)
    return FileResponse(served, media_type=content_type(target), headers=headers)


def resolve(dist: Path, path: str) -> Path | None:
    """The file `path` names inside `dist`, or None. Never a .br/.gz asked for by name."""
    root = dist.resolve()
    target = (root / path).resolve()
    if path and root in target.parents and target.is_file() and target.suffix not in (".br", ".gz"):
        return target
    return None


def not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not found.", headers=interview_headers())
