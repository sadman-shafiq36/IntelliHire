from pathlib import Path

import pytest

from app.services.cv_text import detect_language, read_cv
from app.services.documents import DocumentReadError
from tests import cv_fixtures as fx


def test_reads_a_normal_pdf(tmp_path: Path):
    cv = read_cv(fx.write(tmp_path, "cv.pdf", fx.pdf()))
    assert "Built REST services in Django" in cv.text
    assert cv.language == "en"
    assert cv.used_ocr is False
    assert cv.page_count == 1


def test_two_columns_are_read_one_after_the_other(tmp_path: Path):
    cv = read_cv(fx.write(tmp_path, "cv.pdf", fx.pdf(two_column=True)))
    # the main column comes out whole, before the sidebar, not interleaved line by line
    experience = cv.text.index("Built REST services in Django")
    skills = cv.text.index("Skills")
    assert experience < skills
    assert "Designed PostgreSQL schemas and tuned slow queries." in " ".join(cv.text.split())


def test_a_scanned_pdf_is_read_with_ocr(tmp_path: Path):
    cv = read_cv(fx.write(tmp_path, "scan.pdf", fx.scanned_pdf()))
    assert cv.used_ocr is True
    assert "Django" in cv.text
    assert cv.language == "en"


def test_reads_docx(tmp_path: Path):
    cv = read_cv(fx.write(tmp_path, "cv.docx", fx.docx_bytes()))
    assert "built Flask APIs" in cv.text


def test_a_damaged_file_fails_with_a_plain_reason(tmp_path: Path):
    with pytest.raises(DocumentReadError, match="damaged"):
        read_cv(fx.write(tmp_path, "cv.pdf", fx.pdf()[:400]))


def test_unsupported_type_is_refused(tmp_path: Path):
    with pytest.raises(DocumentReadError, match="PDF and Word"):
        read_cv(fx.write(tmp_path, "cv.png", b"\x89PNG"))


@pytest.mark.parametrize(
    "maker, expected",
    [(fx.spanish_pdf, "other"), (fx.bangla_pdf, "other"), (fx.pdf, "en")],
)
def test_language_of_whole_cvs(tmp_path: Path, maker, expected):
    assert read_cv(fx.write(tmp_path, "cv.pdf", maker())).language == expected


def test_english_with_some_foreign_words_is_still_english():
    text = (
        "Senior engineer with experience in the design of Python services. Led the team that "
        "built a booking platform for a client in Madrid, la plataforma de reservas."
    )
    assert detect_language(text) == "en"
