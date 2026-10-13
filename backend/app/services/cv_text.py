# turns a cv file into plain text. handles two-column pdf layouts, falls back to ocr
# for scanned pdfs, and flags text that doesn't look like english

import re
from dataclasses import dataclass
from pathlib import Path

from app.logging_config import get_logger
from app.services.documents import DocumentReadError, normalise_whitespace

log = get_logger(__name__)

# below this many characters a pdf page is treated as an image and ocr'd
MIN_TEXT_CHARS = 40
OCR_DPI = 200
MAX_PAGES = 10

ENGLISH_WORDS = {
    "the", "and", "of", "to", "in", "for", "with", "on", "at", "as", "by", "from", "my",
    "experience", "skills", "education", "work", "team", "years", "developed", "built",
    "managed", "led", "university", "project", "projects", "using", "responsible", "summary",
}  # fmt: skip

# common words of other languages cvs here are likely to be written in. tech terms are
# english everywhere, so the decision compares these against the english list
OTHER_WORDS = {
    # spanish / portuguese
    "de", "la", "el", "los", "las", "y", "con", "para", "una", "del", "experiencia",
    "habilidades", "formación", "desarrollé", "diseñé", "em", "do", "da", "com", "os",
    # french
    "le", "les", "et", "des", "du", "pour", "avec", "une", "expérience", "compétences",
    # german
    "und", "der", "die", "das", "mit", "für", "von", "erfahrung", "kenntnisse",
}  # fmt: skip


@dataclass(frozen=True)
class CvText:
    text: str
    page_count: int
    used_ocr: bool
    language: str  # "en" or "other"


def read_cv(path: Path) -> CvText:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            text, pages, used_ocr = _pdf(path)
        elif suffix == ".docx":
            text, pages, used_ocr = _docx(path), 1, False
        else:
            raise DocumentReadError("Only PDF and Word (.docx) CVs can be read.")
    except DocumentReadError:
        raise
    except Exception as exc:
        log.info("cv_read_failed", suffix=suffix, error_type=type(exc).__name__)
        raise DocumentReadError("The file could not be opened. It may be damaged.") from exc

    text = normalise_whitespace(text)
    if len(text) < MIN_TEXT_CHARS:
        raise DocumentReadError(
            "No readable text was found in this CV, even with text recognition."
            if used_ocr
            else "No readable text was found in this CV."
        )
    return CvText(text=text, page_count=pages, used_ocr=used_ocr, language=detect_language(text))


def detect_language(text: str) -> str:
    words = re.findall(r"[^\W\d_]+", text.lower())
    letters = sum(len(w) for w in re.findall(r"[a-zA-Z]+", text))
    # mostly non-latin script, e.g. bangla or arabic
    if not words or letters < 0.5 * len(re.sub(r"\s", "", text)):
        return "other"
    english = sum(1 for w in words if w in ENGLISH_WORDS)
    other = sum(1 for w in words if w in OTHER_WORDS)
    return "en" if english >= 3 and english >= other else "other"


def _docx(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = []
            for cell in row.cells:
                if cell.text not in cells:
                    cells.append(cell.text)
            parts.append("\n".join(cells))
    return "\n".join(parts)


def _pdf(path: Path) -> tuple[str, int, bool]:
    import pdfplumber

    pages_text: list[str] = []
    ocr_pages: list[int] = []
    with pdfplumber.open(str(path)) as pdf:
        page_count = len(pdf.pages)
        for index, page in enumerate(pdf.pages[:MAX_PAGES]):
            text = _page_text(page)
            if len(text.strip()) < MIN_TEXT_CHARS:
                ocr_pages.append(index)
                text = ""
            pages_text.append(text)

    if ocr_pages:
        for index, text in _ocr(path, ocr_pages).items():
            pages_text[index] = text
    return "\n\n".join(pages_text), page_count, bool(ocr_pages)


def _page_text(page) -> str:
    # a two-column cv read line by line interleaves the columns. if there is a clear
    # vertical gap that no word crosses, read the left column and then the right
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    if len(words) < 30:
        return page.extract_text() or ""
    split = _column_split(words, page.width)
    if split is None:
        return page.extract_text() or ""
    left = page.crop((0, 0, split, page.height)).extract_text() or ""
    right = page.crop((split, 0, page.width, page.height)).extract_text() or ""
    return f"{left}\n\n{right}"


def _column_split(words: list[dict], width: float) -> float | None:
    # look for an empty vertical band in the middle 30-70% of the page
    resolution = 4
    bins = [0] * (int(width // resolution) + 1)
    for w in words:
        for b in range(int(w["x0"] // resolution), int(w["x1"] // resolution) + 1):
            if b < len(bins):
                bins[b] += 1
    lo, hi = int(len(bins) * 0.3), int(len(bins) * 0.7)
    best_start, best_len, run_start = None, 0, None
    for b in range(lo, hi + 1):
        if bins[b] == 0:
            run_start = b if run_start is None else run_start
            length = b - run_start + 1
            if length > best_len:
                best_start, best_len = run_start, length
        else:
            run_start = None
    # a real gutter is at least ~12pt wide, and both sides must hold real text
    if best_start is None or best_len * resolution < 12:
        return None
    split = (best_start + best_len / 2) * resolution
    left = sum(1 for w in words if w["x1"] <= split)
    right = sum(1 for w in words if w["x0"] >= split)
    if min(left, right) < 0.2 * len(words):
        return None
    return split


def _ocr(path: Path, pages: list[int]) -> dict[int, str]:
    import pytesseract
    from pdf2image import convert_from_path

    out: dict[int, str] = {}
    for index in pages:
        images = convert_from_path(
            str(path), dpi=OCR_DPI, first_page=index + 1, last_page=index + 1
        )
        out[index] = pytesseract.image_to_string(images[0], lang="eng") if images else ""
    log.info("cv_ocr_used", pages=len(pages))
    return out
