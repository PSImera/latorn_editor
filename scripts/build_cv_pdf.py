"""Сборка PDF резюме из markdown-исходника в `docs/`.

Источник истины — `docs/cv-gorzenkov-*.md`. Скрипт разбирает то подмножество
markdown, которое там используется (заголовки, абзацы, списки, таблицы,
жирный, курсив, ссылки), раскладывает его по печатной вёрстке A4 и рендерит
через headless Edge/Chrome — тот же браузер, которым проект растрирует SVG.

Вёрстка повторяет прежнее резюме: крупное имя, узкий правый сайдбар с
контактами и языками, акцентный `#009dff` на ссылках и подписях, серые
подписи разделов. Сайдбар зафиксирован (`position: fixed`) и потому
повторяется на каждой печатной странице.

Запуск:
    .\\.venv\\Scripts\\python.exe scripts\\build_cv_pdf.py
    .\\.venv\\Scripts\\python.exe scripts\\build_cv_pdf.py docs\\cv-gorzenkov-ru.md
"""

from __future__ import annotations

import html
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.latorn_downloader.media_convert import ConversionError, find_browser  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES = [
    ROOT / "docs" / "cv-gorzenkov-ru.md",
    ROOT / "docs" / "cv-gorzenkov-en.md",
]

SKILL_SECTIONS = {"технический стек", "technical skills"}
SIDEBAR_SECTIONS = {"языки", "languages"}

LABELS = {
    "ru": {"details": "Контакты", "links": "Ссылки"},
    "en": {"details": "Details", "links": "Links"},
}

CSS = """
@page { size: A4; margin: 11mm 12mm 10mm 12mm; }

:root {
    --ink: #14161a;
    --body: #34383f;
    --muted: #949494;
    --label: #a9adb3;
    --accent: #009dff;
    --rule: #e0e0e0;
    --sidebar: 45mm;
    --gutter: 7mm;
}

* { box-sizing: border-box; }

body {
    margin: 0;
    font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
    font-size: 8.7pt;
    line-height: 1.40;
    color: var(--body);
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}

a { color: var(--accent); text-decoration: none; }

aside {
    float: right;
    width: var(--sidebar);
    margin: 0 0 4mm var(--gutter);
    padding-left: 4.5mm;
    border-left: 0.6pt solid var(--rule);
}

h1 {
    margin: 0 0 1.2mm;
    font-size: 23pt;
    font-weight: 300;
    letter-spacing: -0.2pt;
    line-height: 1.04;
    color: var(--ink);
}

.role {
    margin: 0 0 0.7mm;
    font-size: 9.9pt;
    font-weight: 400;
    color: var(--accent);
}

.location {
    margin: 0 0 4.4mm;
    font-size: 8.2pt;
    color: var(--muted);
}

h2 {
    margin: 4.9mm 0 1.9mm;
    font-size: 8.1pt;
    font-weight: 600;
    letter-spacing: 1.4pt;
    text-transform: uppercase;
    color: var(--ink);
    break-after: avoid;
}

aside h2 {
    margin: 0 0 2mm;
    font-size: 7.8pt;
    color: var(--label);
}

aside section { margin-bottom: 5mm; }

aside p { margin: 0; font-size: 8.1pt; line-height: 1.6; }

h3 {
    margin: 3.5mm 0 0.3mm;
    font-size: 9.9pt;
    font-weight: 600;
    color: var(--ink);
    break-after: avoid;
}

p.meta {
    margin: 0 0 1.4mm;
    font-size: 8.1pt;
    color: var(--muted);
}

p { margin: 0 0 1.5mm; }

hr { display: none; }

ul { margin: 0 0 2mm; padding-left: 4.2mm; }

li { margin-bottom: 0.8mm; padding-left: 0.5mm; }

li::marker { color: var(--accent); }

.skill {
    display: flex;
    gap: 2.6mm;
    margin-bottom: 1.1mm;
    break-inside: avoid;
}

.skill-label {
    flex: 0 0 27mm;
    font-weight: 600;
    font-size: 8.1pt;
    line-height: 1.46;
    color: var(--accent);
}

.skill-value { flex: 1; font-size: 8.2pt; }

.entry { break-inside: avoid; }

.note {
    margin: 1.5mm 0 0;
    padding-left: 2.6mm;
    border-left: 1.4pt solid var(--rule);
    font-size: 8.4pt;
    color: var(--muted);
}

table {
    width: 100%;
    border-collapse: collapse;
    font-size: 7.7pt;
    margin-bottom: 2mm;
}

thead th {
    padding: 1mm 2mm 1mm 0;
    border-bottom: 0.8pt solid var(--rule);
    text-align: left;
    font-size: 7.4pt;
    font-weight: 600;
    letter-spacing: 0.7pt;
    text-transform: uppercase;
    color: var(--label);
}

tbody td {
    padding: 1.2mm 2mm 1.2mm 0;
    border-bottom: 0.5pt solid #f0f1f3;
    vertical-align: top;
}

tbody tr { break-inside: avoid; }

tbody td:first-child { width: 33%; padding-right: 3mm; color: var(--ink); }

tbody td:nth-child(2) { width: 17%; color: var(--muted); }
"""

PAGE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<aside>
{sidebar}
</aside>
<main>
{body}
</main>
</body>
</html>
"""


def inline(text: str) -> str:
    """Жирный, курсив и ссылки внутри строки."""
    out = html.escape(text, quote=False)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    return out


def render_table(rows: list[str]) -> str:
    """Markdown-таблица → HTML. Вторая строка — разделитель, её пропускаем."""

    def cells(row: str) -> list[str]:
        return [inline(c.strip()) for c in row.strip().strip("|").split("|")]

    head = "".join(f"<th>{c}</th>" for c in cells(rows[0]))
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in cells(row)) + "</tr>"
        for row in rows[2:]
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_paragraph(lines: list[str], section: str) -> str:
    """Абзац: пара «метка — значение» в блоке навыков, примечание или проза."""
    joined = "<br>".join(inline(line) for line in lines)
    raw = " ".join(lines)

    label = re.match(r"\*\*(.+?)\*\*\s+(.+)", raw, flags=re.DOTALL)
    if label and section in SKILL_SECTIONS:
        return (
            '<div class="skill">'
            f'<span class="skill-label">{inline(label.group(1))}</span>'
            f'<span class="skill-value">{inline(label.group(2))}</span>'
            "</div>"
        )

    if len(lines) == 1 and raw.startswith("*") and raw.endswith("*") and not raw.startswith("**"):
        return f'<p class="note">{inline(raw.strip("*"))}</p>'

    if re.fullmatch(r"\*\*[^*]+\*\*", raw.strip()):
        return f'<p class="meta">{joined}</p>'

    return f"<p>{joined}</p>"


class Document:
    """Разобранный markdown: шапка отдельно, разделы отдельно."""

    def __init__(self) -> None:
        self.title = ""
        self.role = ""
        self.location = ""
        self.contact_lines: list[str] = []
        self.sections: list[tuple[str, list[str]]] = []


def parse(text: str) -> Document:
    """Разбор используемого в резюме подмножества markdown."""
    doc = Document()
    parts: list[str] = []
    buffer: list[str] = []
    table: list[str] = []
    bullets: list[str] = []
    section = ""
    header_index = 0
    entry_open = False

    def flush_paragraph() -> None:
        nonlocal buffer, header_index
        if not buffer:
            return
        if not doc.sections and header_index < 3:
            raw = " ".join(buffer)
            if header_index == 0:
                doc.role = inline(raw.strip("*"))
            elif header_index == 1:
                doc.location = inline(raw)
            else:
                doc.contact_lines = [inline(line) for line in buffer]
            header_index += 1
        else:
            parts.append(render_paragraph(buffer, section.strip().lower()))
        buffer = []

    def flush_bullets() -> None:
        nonlocal bullets
        if bullets:
            items = "".join(f"<li>{inline(b)}</li>" for b in bullets)
            parts.append(f"<ul>{items}</ul>")
            bullets = []

    def flush_table() -> None:
        nonlocal table
        if table:
            parts.append(render_table(table))
            table = []

    def flush_all() -> None:
        flush_paragraph()
        flush_bullets()
        flush_table()

    def close_entry() -> None:
        nonlocal entry_open
        if entry_open:
            parts.append("</div>")
            entry_open = False

    def close_section() -> None:
        nonlocal parts
        close_entry()
        if section:
            doc.sections.append((section, parts))
        parts = []

    for line in text.splitlines():
        stripped = line.strip()

        if not stripped:
            flush_all()
            continue

        if stripped.startswith("|"):
            flush_paragraph()
            flush_bullets()
            table.append(stripped)
            continue
        flush_table()

        if stripped.startswith("- "):
            flush_paragraph()
            bullets.append(stripped[2:])
            continue
        flush_bullets()

        if stripped == "---":
            flush_all()
            close_entry()
            continue

        if stripped.startswith("### "):
            flush_all()
            close_entry()
            entry_open = True
            parts.append('<div class="entry">')
            parts.append(f"<h3>{inline(stripped[4:])}</h3>")
            continue

        if stripped.startswith("## "):
            flush_all()
            close_section()
            section = stripped[3:].strip()
            continue

        if stripped.startswith("# "):
            flush_all()
            doc.title = inline(stripped[2:])
            continue

        buffer.append(stripped)

    flush_all()
    close_section()
    return doc


def compose(doc: Document, lang: str) -> tuple[str, str]:
    """Раскладка разобранного документа по основной колонке и сайдбару."""
    labels = LABELS[lang]

    main = [f"<h1>{doc.title}</h1>", f'<p class="role">{doc.role}</p>']
    if doc.location:
        main.append(f'<p class="location">{doc.location}</p>')

    def stacked(lines: list[str]) -> str:
        """В узкой колонке каждый пункт с отдельной строки, без разделителей."""
        items = [part.strip() for line in lines for part in line.split("·")]
        return "<br>".join(item for item in items if item)

    sidebar: list[str] = []
    if doc.contact_lines:
        sidebar.append(
            f'<section><h2>{labels["details"]}</h2>'
            f"<p>{stacked(doc.contact_lines[:1])}</p></section>"
        )
    if len(doc.contact_lines) > 1:
        sidebar.append(
            f'<section><h2>{labels["links"]}</h2>'
            f"<p>{stacked(doc.contact_lines[1:])}</p></section>"
        )

    for name, blocks in doc.sections:
        rendered = f"<h2>{inline(name)}</h2>\n" + "\n".join(blocks)
        if name.strip().lower() in SIDEBAR_SECTIONS:
            sidebar.append(f'<section>{rendered.replace(" · ", "<br>")}</section>')
        else:
            main.append(rendered)

    return "\n".join(main), "\n".join(sidebar)


def build(source: Path) -> Path:
    """Markdown → PDF рядом с исходником."""
    doc = parse(source.read_text(encoding="utf-8"))
    lang = "en" if source.stem.endswith("-en") else "ru"
    body, sidebar = compose(doc, lang)
    page = PAGE.format(
        lang=lang,
        title=re.sub(r"<[^>]+>", "", doc.title) or source.stem,
        css=CSS,
        body=body,
        sidebar=sidebar,
    )

    out_path = source.with_suffix(".pdf")
    browser = find_browser()

    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "cv.html"
        html_path.write_text(page, encoding="utf-8")
        cmd = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--user-data-dir={Path(tmp) / 'profile'}",
            f"--print-to-pdf={out_path}",
            html_path.as_uri(),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if not out_path.exists():
        raise ConversionError(
            f"браузер не создал {out_path.name}: {result.stderr.strip()[-500:]}"
        )
    return out_path


def main(argv: list[str]) -> int:
    sources = [Path(a) for a in argv[1:]] or DEFAULT_SOURCES
    for source in sources:
        if not source.exists():
            print(f"нет файла: {source}")
            return 1
        out = build(source)
        size_kb = out.stat().st_size / 1024
        print(f"{source.name} -> {out.name} ({size_kb:.0f} КБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
