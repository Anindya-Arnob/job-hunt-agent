"""Word -> PDF. LibreOffice (headless) if installed, otherwise Microsoft Word via docx2pdf on Windows."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

_lock = threading.Lock()

WINDOWS_SOFFICE = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]
# macOS: LibreOffice.app does not put `soffice` on the PATH
MAC_SOFFICE = [
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    os.path.expanduser("~/Applications/LibreOffice.app/Contents/MacOS/soffice"),
]
MAC_WORD = ["/Applications/Microsoft Word.app", os.path.expanduser("~/Applications/Microsoft Word.app")]


class PdfUnavailable(RuntimeError):
    pass


def find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for p in WINDOWS_SOFFICE if sys.platform == "win32" else MAC_SOFFICE if sys.platform == "darwin" else []:
        if os.path.exists(p):
            return p
    return None


def word_available() -> bool:
    """Microsoft Word can make the PDF instead of LibreOffice (Windows, or a Mac with Word installed)."""
    if sys.platform == "win32":
        return True
    return sys.platform == "darwin" and any(os.path.exists(p) for p in MAC_WORD)


def pdf_available() -> bool:
    return find_soffice() is not None or word_available()


def to_pdf(docx_path: Path) -> Path:
    docx_path = Path(docx_path)
    out = docx_path.with_suffix(".pdf")
    soffice = find_soffice()
    if soffice:
        with _lock, tempfile.TemporaryDirectory() as profile, tempfile.TemporaryDirectory() as outdir:
            cmd = [soffice, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless",
                   "--convert-to", "pdf", "--outdir", outdir, str(docx_path)]
            subprocess.run(cmd, capture_output=True, timeout=180, check=False)
            produced = Path(outdir) / (docx_path.stem + ".pdf")
            if not produced.exists():
                raise PdfUnavailable(f"LibreOffice could not convert {docx_path.name}")
            shutil.move(str(produced), str(out))
        return out
    if word_available():
        try:
            from docx2pdf import convert  # uses Microsoft Word (on a Mac it asks once to let Terminal control Word)
        except ImportError as e:
            raise PdfUnavailable("Install LibreOffice, or `pip install docx2pdf` (needs Microsoft Word)") from e
        with _lock:
            try:
                import pythoncom  # Word automation needs COM set up in this thread
                pythoncom.CoInitialize()
            except ImportError:
                pythoncom = None
            try:
                convert(str(docx_path), str(out))
            finally:
                if pythoncom:
                    pythoncom.CoUninitialize()
        if not out.exists():
            raise PdfUnavailable(f"Word could not convert {docx_path.name}")
        return out
    raise PdfUnavailable("LibreOffice not found. Install it from libreoffice.org"
                         + (" (or: brew install --cask libreoffice)." if sys.platform == "darwin" else "."))
