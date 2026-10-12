import io
from pathlib import Path

import pytest

from app.services.documents import DocumentReadError, chunk_text, extract_text


def test_chunks_stay_within_the_limit():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 40 for i in range(30))
    chunks = chunk_text(text, max_chars=500, overlap=80)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)


def test_short_text_is_one_chunk():
    assert chunk_text("Leave policy.\n\nTwenty days a year.", 1200, 200) == [
        "Leave policy.\n\nTwenty days a year."
    ]


def test_consecutive_chunks_overlap():
    text = "\n\n".join(f"Section {i} " + "alpha beta gamma " * 20 for i in range(10))
    chunks = chunk_text(text, max_chars=400, overlap=100)
    # the start of each chunk repeats words from the end of the previous one
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert current.split()[0] in previous


def test_a_paragraph_longer_than_the_limit_is_split():
    paragraph = " ".join(f"Sentence number {i} is here." for i in range(100))
    chunks = chunk_text(paragraph, max_chars=300, overlap=0)
    assert len(chunks) > 1
    assert all(len(c) <= 300 for c in chunks)
    assert "Sentence number 99" in chunks[-1]


def test_reads_plain_text(tmp_path: Path):
    path = tmp_path / "policy.txt"
    path.write_text("Remote work\r\n\r\n\r\n\r\nUp to   three days.", encoding="utf-8")
    assert extract_text(path) == "Remote work\n\nUp to three days."


def test_reads_docx_paragraphs_and_tables(tmp_path: Path):
    import docx

    document = docx.Document()
    document.add_paragraph("Annual leave is 20 days.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Grade"
    table.rows[0].cells[1].text = "Band B"
    path = tmp_path / "policy.docx"
    document.save(path)

    text = extract_text(path)
    assert "Annual leave is 20 days." in text
    assert "Grade | Band B" in text


def test_a_pdf_without_text_fails_with_a_readable_message(tmp_path: Path):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    path = tmp_path / "scan.pdf"
    path.write_bytes(buffer.getvalue())

    with pytest.raises(DocumentReadError, match="scanned image"):
        extract_text(path)


def test_a_damaged_file_fails_safely(tmp_path: Path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not really a docx")
    with pytest.raises(DocumentReadError, match="damaged"):
        extract_text(path)
