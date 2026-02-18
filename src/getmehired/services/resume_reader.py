"""
Resume text extraction service.

Supports:
  - PDF files (.pdf) via pdfplumber — page-by-page text, joined with newlines
  - Plain text files (.txt) — read directly

Returns a single string of extracted text, suitable for passing to an LLM.
"""
from __future__ import annotations

from pathlib import Path


def read_resume(path: Path) -> str:
    """
    Extract text from a resume file.

    Args:
        path: Path to a .pdf or .txt file.

    Returns:
        Extracted text as a single string.

    Raises:
        ValueError: If the file extension is not supported.
        FileNotFoundError: If the file does not exist.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Resume file not found: {path}")

    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return _read_pdf(path)
    elif suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace").strip()
    else:
        raise ValueError(
            f"Unsupported resume format: '{suffix}'. Use a .pdf or .txt file."
        )


def _read_pdf(path: Path) -> str:
    """Extract text from a PDF file using pdfplumber."""
    try:
        import pdfplumber
    except ImportError as e:
        raise ImportError(
            "pdfplumber is required to read PDF resumes. "
            "Install it with: pip install pdfplumber"
        ) from e

    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text.strip())

    return "\n\n".join(pages).strip()
