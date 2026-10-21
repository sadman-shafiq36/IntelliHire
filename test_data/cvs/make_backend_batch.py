# generates the synthetic "backend engineer" cv batch used for screening tests and the
# 20-cv timing run. every person here is invented. run inside the backend container:
#   docker compose run --rm --no-deps -v "<repo>/test_data:/test_data" backend \
#       python /test_data/cvs/make_backend_batch.py

import io
import random
from pathlib import Path

OUT = Path(__file__).parent / "backend-engineer-batch"

# (file stem, layout, name, email, headline, summary, experience, skills, education, certs)
PEOPLE = [
    ("01-farhan-kabir", "single", "Farhan Kabir", "farhan.kabir@example.com",
     "Senior Backend Engineer",
     "Backend engineer with 7 years building Python services for payments and logistics.",
     [("Senior Backend Engineer", "Kestrel Payments", "2021 - present",
       ["Built REST services in Django serving 3 million requests a day.",
        "Designed PostgreSQL schemas and tuned slow queries, cutting p95 latency by 40%.",
        "Mentored four engineers and ran the team's code reviews."]),
      ("Backend Developer", "Riverbend Logistics", "2018 - 2021",
       ["Wrote Flask APIs for shipment tracking.",
        "Moved deployments to Docker and GitHub Actions."])],
     "Python, Django, Flask, PostgreSQL, Redis, Docker, GitHub Actions, AWS",
     "BSc Computer Science, Northfield University, 2018", "AWS Certified Developer"),
    ("02-meera-iyer", "single", "Meera Iyer", "meera.iyer@example.com",
     "Python Developer",
     "Python developer focused on FastAPI microservices and data-heavy backends.",
     [("Software Engineer", "Lumen Health", "2020 - present",
       ["Developed FastAPI microservices for patient scheduling.",
        "Owned the MySQL to PostgreSQL migration of 120 tables.",
        "Set up CI/CD pipelines with GitLab and Kubernetes."])],
     "Python, FastAPI, SQLAlchemy, PostgreSQL, MySQL, Kubernetes, Docker, GCP",
     "BSc Software Engineering, Coastline Institute, 2019", None),
    ("03-tomas-reyes", "two-column", "Tomas Reyes", "tomas.reyes@example.com",
     "Backend Engineer",
     "Engineer who enjoys clean APIs and reliable databases.",
     [("Backend Engineer", "Brightlane Retail", "2019 - present",
       ["Maintained a Django e-commerce backend and its PostgreSQL database.",
        "Introduced Docker-based local development and automated tests in CI.",
        "Deployed services to Azure App Service."])],
     "Python, Django, Django REST Framework, PostgreSQL, Celery, Docker, Azure",
     "BEng Computer Engineering, Harbour Technical University, 2019", None),
    ("04-aisha-noor", "scanned", "Aisha Noor", "aisha.noor@example.com",
     "Software Engineer (Backend)",
     "Backend engineer with five years of Python and SQL experience.",
     [("Software Engineer", "Quillstone Media", "2020 - present",
       ["Built Flask services that publish articles to 40 regional sites.",
        "Designed relational schemas in PostgreSQL and wrote reporting queries.",
        "Containerised the platform with Docker."])],
     "Python, Flask, PostgreSQL, SQL, Docker, Linux",
     "BSc Information Technology, Eastmoor University, 2019", None),
    ("05-li-wei", "docx", "Li Wei", "li.wei@example.com",
     "Full-Stack Developer",
     "Full-stack developer, mostly backend, with three years of Python.",
     [("Full-Stack Developer", "Orchard Learning", "2022 - present",
       ["Built course enrolment APIs in FastAPI.",
        "Wrote React screens for teachers.",
        "Used SQLite in production, now migrating to PostgreSQL."])],
     "Python, FastAPI, React, TypeScript, SQLite, PostgreSQL (learning)",
     "BSc Computer Science, Westgate University, 2021", None),
    ("06-grace-mensah", "single", "Grace Mensah", "grace.mensah@example.com",
     "Backend Developer",
     "Backend developer with experience in Node.js and some Python scripting.",
     [("Backend Developer", "Tidewater Travel", "2020 - present",
       ["Built Express.js APIs for hotel bookings backed by PostgreSQL.",
        "Wrote Python scripts to clean booking data.",
        "Deployed to AWS with Docker and CodePipeline."])],
     "Node.js, Express, TypeScript, PostgreSQL, Python (scripting), Docker, AWS",
     "BSc Computer Science, Southgate University, 2019", None),
    ("07-omar-haddad", "no-education", "Omar Haddad", "omar.haddad@example.com",
     "Python Engineer",
     "Self-taught engineer with six years of shipping Python web applications.",
     [("Lead Developer", "Copperleaf Studios", "2021 - present",
       ["Led a team of five building Django applications for clients.",
        "Designed PostgreSQL data models and wrote migrations.",
        "Coached junior developers through pair programming."]),
      ("Developer", "Freelance", "2018 - 2021",
       ["Built Flask and Django sites for small businesses."])],
     "Python, Django, Flask, PostgreSQL, Docker, Heroku", None, None),
    ("08-sofia-rossi", "single", "Sofia Rossi", "sofia.rossi@example.com",
     "Frontend Engineer",
     "Frontend engineer specialising in accessible React interfaces.",
     [("Frontend Engineer", "Bluefin Analytics", "2019 - present",
       ["Built React dashboards for analytics customers.",
        "Improved accessibility scores to WCAG AA."])],
     "JavaScript, TypeScript, React, CSS, Figma, Jest",
     "BA Interaction Design, Lakeside College, 2018", None),
    ("09-daniel-okafor", "single", "Daniel Okafor", "daniel.okafor@example.com",
     "Data Analyst",
     "Data analyst who turns SQL queries into business decisions.",
     [("Data Analyst", "Greenway Energy", "2020 - present",
       ["Wrote complex SQL queries against a PostgreSQL warehouse.",
        "Automated weekly reports with Python and pandas.",
        "Built Tableau dashboards for the operations team."])],
     "SQL, PostgreSQL, Python, pandas, Tableau, Excel",
     "BSc Statistics, Rivermouth University, 2019", None),
    ("10-hannah-schmidt", "single", "Hannah Schmidt", "hannah.schmidt@example.com",
     "Junior Python Developer",
     "Junior developer one year into a first engineering role.",
     [("Junior Developer", "Pinecrest Software", "2025 - present",
       ["Added endpoints to a Django REST API.",
        "Wrote unit tests with pytest."])],
     "Python, Django, pytest, Git, PostgreSQL (basic)",
     "BSc Computer Science, Highbridge University, 2025", None),
    ("11-rafael-souza", "single", "Rafael Souza", "rafael.souza@example.com",
     "Site Reliability Engineer",
     "SRE with a background in Python tooling and cloud infrastructure.",
     [("Site Reliability Engineer", "Northwind Cloud", "2019 - present",
       ["Wrote Python tooling for deployments and incident response.",
        "Ran Kubernetes clusters on GCP and managed Terraform.",
        "Operated PostgreSQL and Redis in production."])],
     "Python, Go, Kubernetes, Terraform, GCP, PostgreSQL, Redis, Prometheus",
     "BSc Computer Networks, Ironbridge Polytechnic, 2018", "Google Professional Cloud Architect"),
    ("12-chloe-martin", "single", "Chloe Martin", "chloe.martin@example.com",
     "Mobile Developer",
     "iOS and Android developer building consumer apps.",
     [("Mobile Developer", "Sparrow Apps", "2020 - present",
       ["Built iOS apps in Swift and Android apps in Kotlin.",
        "Integrated REST APIs and Firebase."])],
     "Swift, Kotlin, Firebase, REST, Git",
     "BSc Computing, Fairview University, 2020", None),
    ("13-arjun-mehta", "single", "Arjun Mehta", "arjun.mehta@example.com",
     "Backend Engineer",
     "Backend engineer with four years in Python microservices.",
     [("Backend Engineer", "Harborview Insurance", "2021 - present",
       ["Built claim processing services with FastAPI and Celery.",
        "Designed PostgreSQL schemas and partitioned large tables.",
        "Deployed with Docker and Jenkins to AWS ECS."])],
     "Python, FastAPI, Celery, PostgreSQL, Docker, Jenkins, AWS",
     "MSc Computer Science, Granite University, 2021", None),
    ("14-nora-lindqvist", "single", "Nora Lindqvist", "nora.lindqvist@example.com",
     "QA Engineer",
     "QA engineer automating API and UI tests.",
     [("QA Engineer", "Silverline Banking", "2020 - present",
       ["Automated API tests in Python with pytest and requests.",
        "Maintained Selenium suites for the web banking app."])],
     "Python, pytest, Selenium, Postman, SQL",
     "BSc Information Systems, Kingsbridge University, 2019", None),
    ("15-kwame-boateng", "single", "Kwame Boateng", "kwame.boateng@example.com",
     "Software Engineer",
     "Generalist software engineer, Java first, Python second.",
     [("Software Engineer", "Ashford Logistics", "2018 - present",
       ["Built Spring Boot services backed by Oracle and PostgreSQL.",
        "Wrote a Flask admin tool for the operations team.",
        "Mentored two graduates."])],
     "Java, Spring Boot, Python, Flask, Oracle, PostgreSQL, Docker",
     "BSc Computer Science, Meadowbank University, 2017", None),
    ("16-yuki-tanaka", "single", "Yuki Tanaka", "yuki.tanaka@example.com",
     "Backend Engineer",
     "Backend engineer with eight years across Python and Ruby.",
     [("Staff Engineer", "Glasshouse Media", "2020 - present",
       ["Architected Django services for a streaming catalogue of 2 million titles.",
        "Led the PostgreSQL scaling effort with read replicas and partitioning.",
        "Mentored a team of six and ran architecture reviews."]),
      ("Software Engineer", "Redbrick Games", "2016 - 2020",
       ["Built Ruby on Rails backends and moved two services to Python."])],
     "Python, Django, Ruby on Rails, PostgreSQL, Kafka, Docker, AWS, CI/CD",
     "BEng Software Engineering, Summit Technical University, 2016", None),
    ("17-elena-garcia", "spanish", "Elena García", "elena.garcia@example.com",
     "Ingeniera de Backend",
     "Ingeniera de software con cinco años de experiencia en Python.",
     [("Ingeniera de Backend", "Marea Digital", "2020 - actualidad",
       ["Desarrollé servicios REST con Django para una plataforma de reservas.",
        "Diseñé bases de datos PostgreSQL y optimicé consultas lentas.",
        "Configuré integración continua con Docker y GitHub Actions."])],
     "Python, Django, PostgreSQL, Docker, GitHub Actions",
     "Grado en Ingeniería Informática, Universidad de Valleverde, 2019", None),
    ("18-peter-novak", "single", "Peter Novak", "peter.novak@example.com",
     "IT Support Specialist",
     "IT support specialist moving towards software development.",
     [("IT Support Specialist", "Elmwood Council", "2019 - present",
       ["Supported 300 staff with hardware and software issues.",
        "Wrote small Python scripts to automate user provisioning."])],
     "Windows, Active Directory, Python (basic), PowerShell",
     "Diploma in IT Support, Brookside College, 2018", None),
    ("19-amara-diallo", "single", "Amara Diallo", "amara.diallo@example.com",
     "Backend Developer",
     "Backend developer with three years of Flask and PostgreSQL.",
     [("Backend Developer", "Cobalt Fintech", "2022 - present",
       ["Built Flask APIs for loan applications.",
        "Wrote PostgreSQL stored procedures and migrations with Alembic.",
        "Added Docker and GitHub Actions to the team's workflow."])],
     "Python, Flask, PostgreSQL, Alembic, Docker, GitHub Actions",
     "BSc Computer Science, Riverside University, 2021", None),
]


def html_cv(p: tuple, two_column: bool = False) -> str:
    stem, layout, name, email, headline, summary, jobs, skills, education, certs = p
    exp = "".join(
        f"<p><b>{t}</b>, {c} — {d}</p><ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>"
        for t, c, d, bullets in jobs
    )
    spanish = layout == "spanish"
    h = {
        "summary": "Perfil" if spanish else "Summary",
        "exp": "Experiencia" if spanish else "Experience",
        "skills": "Habilidades" if spanish else "Skills",
        "edu": "Formación" if spanish else "Education",
        "certs": "Certificaciones" if spanish else "Certifications",
    }
    side = f"<h2>{h['skills']}</h2><p>{skills}</p>"
    if education:
        side += f"<h2>{h['edu']}</h2><p>{education}</p>"
    if certs:
        side += f"<h2>{h['certs']}</h2><p>{certs}</p>"
    main = f"<h2>{h['summary']}</h2><p>{summary}</p><h2>{h['exp']}</h2>{exp}"
    note = (
        ""
        if spanish
        else "<p class='note'>Synthetic CV for IntelliHire testing. This person does not exist.</p>"
    )
    style = """body{font-family:sans-serif;font-size:10.5pt;margin:36px;color:#222}
    h1{margin:0;font-size:19pt} h2{font-size:11.5pt;border-bottom:1px solid #999;margin-top:14px}
    .muted{color:#555} ul{margin:4px 0 0 16px;padding:0} .note{font-size:8.5pt;color:#777}
    .cols{display:flex;gap:40px} .left{width:58%} .right{width:36%}"""
    head = f"<h1>{name}</h1><div class='muted'>{headline} · {email}</div>{note}"
    if two_column:
        body = f"{head}<div class='cols'><div class='left'>{main}</div><div class='right'>{side}</div></div>"
    else:
        body = head + main + side
    return f"<html><head><style>{style}</style></head><body>{body}</body></html>"


def write_pdf(html: str) -> bytes:
    from weasyprint import HTML

    return HTML(string=html).write_pdf()


def scanned(pdf_bytes: bytes) -> bytes:
    # render to an image, tilt and add speckle so it looks like a scan, save image-only pdf
    from pdf2image import convert_from_bytes
    from PIL import ImageFilter

    rng = random.Random(4)
    pages = []
    for img in convert_from_bytes(pdf_bytes, dpi=150):
        img = img.convert("L").rotate(0.6, expand=True, fillcolor=255)
        px = img.load()
        for _ in range(4000):
            x, y = rng.randrange(img.width), rng.randrange(img.height)
            px[x, y] = rng.choice((150, 200))
        pages.append(img.filter(ImageFilter.SMOOTH).convert("RGB"))
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=150)
    return buf.getvalue()


def write_docx(p: tuple, path: Path) -> None:
    import docx

    stem, layout, name, email, headline, summary, jobs, skills, education, certs = p
    d = docx.Document()
    d.add_heading(name, level=0)
    d.add_paragraph(f"{headline} · {email}")
    d.add_paragraph("Synthetic CV for IntelliHire testing. This person does not exist.")
    d.add_heading("Summary", level=1)
    d.add_paragraph(summary)
    d.add_heading("Experience", level=1)
    for t, c, dates, bullets in jobs:
        d.add_paragraph(f"{t}, {c} — {dates}")
        for b in bullets:
            d.add_paragraph(b, style="List Bullet")
    d.add_heading("Skills", level=1)
    d.add_paragraph(skills)
    if education:
        d.add_heading("Education", level=1)
        d.add_paragraph(education)
    d.save(path)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for p in PEOPLE:
        stem, layout = p[0], p[1]
        if layout == "docx":
            write_docx(p, OUT / f"{stem}.docx")
            continue
        pdf = write_pdf(html_cv(p, two_column=layout == "two-column"))
        if layout == "scanned":
            pdf = scanned(pdf)
        (OUT / f"{stem}.pdf").write_bytes(pdf)
    # the 20th file: a pdf cut off half way, as from a failed download
    good = write_pdf(html_cv(PEOPLE[0]))
    (OUT / "20-corrupt-download.pdf").write_bytes(good[: len(good) // 3])
    print(f"wrote {len(list(OUT.iterdir()))} files to {OUT}")


if __name__ == "__main__":
    main()
