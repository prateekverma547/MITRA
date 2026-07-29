"""Extract plain text from an uploaded JD or CV.

One adapter, three formats. Everything downstream — clarification chat, blueprint
generation — works on plain text and never learns what a PDF is.

Parsing quality matters more than it looks. A CV that extracts as garbage
produces a blueprint full of questions about things the candidate never claimed,
and the failure is silent: the blueprint is well-formed, just wrong. Hence the
sanity check on extracted text rather than trusting whatever comes back.
"""

import io
from pathlib import Path

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

#: Below this, extraction almost certainly failed — a scanned PDF with no text
#: layer typically yields a handful of stray characters.
MIN_USABLE_CHARS = 200

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


class DocumentError(ValueError):
    """Raised when a document cannot be read into usable text."""


def _from_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"Could not read PDF: {exc}") from exc
    return "\n\n".join(pages)


def _from_docx(data: bytes) -> str:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"Could not read DOCX: {exc}") from exc

    blocks = [p.text for p in document.paragraphs]
    # Plenty of real CVs lay everything out in tables; ignoring them loses the
    # entire work history.
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    return "\n".join(blocks)


def _from_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentError("Could not decode text file in utf-8, utf-16 or latin-1.")


def extract_text(*, filename: str, data: bytes) -> str:
    """Turn an uploaded document into plain text.

    Raises:
        DocumentError: unsupported type, unreadable file, or text so short that
            extraction evidently failed.
    """
    if not data:
        raise DocumentError("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentError(
            f"File is {len(data) / 1_048_576:.1f} MB; the limit is "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB."
        )

    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentError(
            f"Unsupported file type '{suffix or filename}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}."
        )

    if suffix == ".pdf":
        text = _from_pdf(data)
    elif suffix == ".docx":
        text = _from_docx(data)
    else:
        text = _from_text(data)

    text = normalise(text)

    if len(text) < MIN_USABLE_CHARS:
        raise DocumentError(
            f"Only {len(text)} characters of text could be extracted from "
            f"'{filename}'. If this is a scanned PDF it has no text layer — "
            "please upload a text-based version."
        )
    return text


def normalise(text: str) -> str:
    """Collapse the whitespace damage that PDF extraction reliably produces."""
    lines = [" ".join(line.split()) for line in text.splitlines()]

    cleaned: list[str] = []
    blank_run = 0
    for line in lines:
        if line:
            blank_run = 0
            cleaned.append(line)
        else:
            blank_run += 1
            if blank_run == 1:
                cleaned.append("")
    return "\n".join(cleaned).strip()
