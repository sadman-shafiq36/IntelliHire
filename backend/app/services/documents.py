import re
from pathlib import Path

from app.logging_config import get_logger

log = get_logger(__name__)


class DocumentReadError(Exception):
    # message is shown to the user
    pass


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            text = _pdf_text(path)
        elif suffix == ".docx":
            text = _docx_text(path)
        elif suffix in {".txt", ".md"}:
            text = path.read_text(encoding="utf-8", errors="replace")
        else:
            raise DocumentReadError("This file type is not supported.")
    except DocumentReadError:
        raise
    except Exception as exc:
        log.warning("document_read_failed", suffix=suffix, error_type=type(exc).__name__)
        raise DocumentReadError("The file could not be opened. It may be damaged.") from exc

    text = normalise_whitespace(text)
    if not text:
        raise DocumentReadError("No text could be read from this file. It may be a scanned image.")
    return text


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _docx_text(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n\n".join(parts)


def normalise_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, max_chars: int, overlap: int) -> list[str]:
    # pack whole paragraphs up to max_chars, only split a paragraph when it's too long
    # on its own. overlap carries the tail of one chunk into the next
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    pieces: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
        else:
            pieces.extend(_split_long(paragraph, max_chars))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n\n{piece}" if current else piece
        if len(candidate) <= max_chars:
            current = candidate
            continue
        chunks.append(current)
        tail = _tail(current, overlap)
        current = (
            f"{tail}\n\n{piece}" if tail and len(tail) + len(piece) + 2 <= max_chars else piece
        )
    if current:
        chunks.append(current)
    return chunks


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    out: list[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > max_chars:
            if current:
                out.append(current)
                current = ""
            out.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        candidate = f"{current} {sentence}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            out.append(current)
            current = sentence
    if current:
        out.append(current)
    return out


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    # start on a word boundary
    space = tail.find(" ")
    return tail[space + 1 :] if 0 <= space < len(tail) - 1 else tail
