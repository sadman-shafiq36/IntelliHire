# small synthetic cvs built on the fly, so tests don't depend on files outside backend/

import io
from pathlib import Path

BODY = """
<h1>{name}</h1><p>{email} · +44 7700 900123</p>
<h2>Summary</h2><p>Backend engineer with five years of experience building Python services.</p>
<h2>Experience</h2>
<p>Backend Engineer, Example Retail, 2020 - present</p>
<ul><li>Built REST services in Django for the online shop.</li>
<li>Designed PostgreSQL schemas and tuned slow queries.</li>
<li>Moved the team to Docker and GitHub Actions.</li></ul>
<h2>Education</h2><p>BSc Computer Science, Example University, 2019</p>
"""

SIDEBAR = """
<h2>Skills</h2><p>Python, Django, PostgreSQL, Docker, Celery, Redis, Linux, Git, AWS, Terraform</p>
<h2>Languages</h2><p>Fluent written and spoken communication for distributed teams.</p>
"""


def pdf(name="Ada Lovelace", email="ada@example.com", two_column=False) -> bytes:
    from weasyprint import HTML

    if two_column:
        body = (
            "<div style='display:flex;gap:48px'>"
            f"<div style='width:58%'>{BODY.format(name=name, email=email)}</div>"
            f"<div style='width:34%'>{SIDEBAR}</div></div>"
        )
    else:
        body = BODY.format(name=name, email=email) + SIDEBAR
    html = f"<html><body style='font-family:sans-serif;font-size:11pt'>{body}</body></html>"
    return HTML(string=html).write_pdf()


def scanned_pdf() -> bytes:
    from pdf2image import convert_from_bytes

    image = convert_from_bytes(pdf(), dpi=150)[0].convert("L").convert("RGB")
    out = io.BytesIO()
    image.save(out, format="PDF", resolution=150)
    return out.getvalue()


def spanish_pdf() -> bytes:
    from weasyprint import HTML

    html = (
        "<html><body><h1>Elena García</h1><h2>Experiencia</h2>"
        "<p>Desarrollé servicios REST con Django para una plataforma de reservas y diseñé "
        "las bases de datos de la empresa con PostgreSQL. Trabajé con el equipo de producto "
        "para mejorar la experiencia de los usuarios.</p><h2>Formación</h2>"
        "<p>Grado en Ingeniería Informática de la Universidad de Valleverde.</p></body></html>"
    )
    return HTML(string=html).write_pdf()


def bangla_pdf() -> bytes:
    # non-latin text without needing a bangla font: the check counts script, not glyphs
    from weasyprint import HTML

    text = "আমি একজন সফটওয়্যার প্রকৌশলী। আমার পাঁচ বছরের অভিজ্ঞতা আছে। " * 8
    return HTML(string=f"<html><body><p>{text}</p></body></html>").write_pdf()


def docx_bytes(name="Grace Hopper") -> bytes:
    import docx

    d = docx.Document()
    d.add_heading(name, level=0)
    d.add_paragraph("Experience: built Flask APIs and designed PostgreSQL databases for 4 years.")
    d.add_paragraph("Skills: Python, Flask, PostgreSQL, Docker")
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def write(tmp: Path, name: str, data: bytes) -> Path:
    path = tmp / name
    path.write_bytes(data)
    return path
