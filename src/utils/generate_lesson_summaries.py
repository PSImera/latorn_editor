from __future__ import annotations

import argparse
import html
import json
import math
import re
from collections import Counter
from pathlib import Path

from nltk.corpus import stopwords

TAG_RE = re.compile(r"<[^>]+>")
LEADING_INDEX_RE = re.compile(r"^(\d+)")

STOPWORDS: set[str] = set(stopwords.words("russian")) | set(stopwords.words("english"))

# Типы шагов по категориям
TEXT_TYPES = {"text"}
TASK_TYPES = {"code", "dataset", "sql", "html"}
REVIEW_TYPES = {"free-answer"}
QUIZ_TYPES = {"choice", "matching", "sorting", "fill-blanks", "number", "string", "math", "table", "random-tasks"}


# ---------------------------------------------------------------------------
# Утилиты для работы с HTML
# ---------------------------------------------------------------------------

def clean_html(value: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", value, flags=re.I | re.S)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def first_sentences(text: str, max_sentences: int = 2, max_chars: int = 280) -> str:
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text)
    result = " ".join(parts[:max_sentences]).strip()
    if len(result) > max_chars:
        result = result[: max_chars - 3].rstrip() + "..."
    return result


def extract_tag(html_text: str, tag: str) -> list[str]:
    pattern = re.compile(rf"<{tag}\b[^>]*>(.*?)</{tag}>", flags=re.I | re.S)
    return [clean_html(m) for m in pattern.findall(html_text) if clean_html(m)]


def read_html(path: Path) -> str:
    if not path.exists():
        return ""
    raw = path.read_text(encoding="utf-8")
    body = re.search(r"<body[^>]*>(.*?)</body>", raw, re.I | re.S)
    return (body.group(1) if body else raw).strip()


def read_step_text(folder: Path) -> str:
    """Текст шага: task.md, если он есть, иначе task.html."""
    md_path = folder / "task.md"
    if md_path.exists():
        return md_path.read_text(encoding="utf-8").strip()
    return read_html(folder / "task.html")


def extract_paragraphs(step_text: str) -> list[str]:
    """Абзацы шага: <p> для HTML, блоки между пустыми строками для markdown."""
    paragraphs = extract_tag(step_text, "p")
    if paragraphs:
        return paragraphs
    blocks = re.split(r"\n\s*\n", strip_code_blocks(step_text))
    result = []
    for block in blocks:
        block = block.strip()
        if not block or block.startswith("#"):
            continue
        result.append(clean_html(block))
    return result


def title_from_html(html_text: str, fallback: str = "") -> str:
    m = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", html_text, re.I | re.S)
    if m:
        return clean_html(m.group(1))
    m = re.search(r"^#{1,3}\s+(.+)$", html_text, re.M)
    if m:
        return clean_html(m.group(1))
    paragraphs = extract_paragraphs(html_text)
    if paragraphs:
        return first_sentences(paragraphs[0], max_sentences=1, max_chars=100)
    return fallback


def title_from_json_text(text_html: str, fallback: str = "") -> str:
    return title_from_html(text_html, fallback)


# ---------------------------------------------------------------------------
# Индекс курса для TF-IDF (строится один раз на весь запуск)
# ---------------------------------------------------------------------------

def strip_code_blocks(value: str) -> str:
    """Удаляет код перед токенизацией: <pre>/<code> в HTML, fences и `...` в markdown."""
    value = re.sub(r"<pre\b[^>]*>.*?</pre>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<code\b[^>]*>.*?</code>", " ", value, flags=re.I | re.S)
    value = re.sub(r"```.*?```", " ", value, flags=re.S)
    value = re.sub(r"`[^`\n]+`", " ", value)
    return value


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-zа-яёA-ZА-ЯЁ][a-zа-яёA-ZА-ЯЁ0-9\-]*", text)
    return [w.lower() for w in words if w.lower() not in STOPWORDS]


def build_course_idf(course_root: Path) -> dict[str, float]:
    """Считает IDF по всем task.html/task.md файлам курса (без кода)."""
    files = list(course_root.rglob("task.html")) + list(course_root.rglob("task.md"))
    N = len(files)
    if N == 0:
        return {}
    df: Counter[str] = Counter()
    for f in files:
        text = clean_html(strip_code_blocks(f.read_text(encoding="utf-8")))
        for w in set(tokenize(text)):
            df[w] += 1
    return {w: math.log(N / cnt) for w, cnt in df.items()}


def top_terms(texts: list[str], idf: dict[str, float], limit: int = 10) -> list[str]:
    """Возвращает топ-N ключевых слов урока по TF-IDF."""
    tokens = tokenize(" ".join(texts))
    if not tokens:
        return []
    tf = Counter(tokens)
    scores = {w: tf[w] * idf.get(w, 0.0) for w in tf}
    return [w for w, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]]


# ---------------------------------------------------------------------------
# Чтение структуры курса из structure.json
# ---------------------------------------------------------------------------

def load_lesson_titles(course_root: Path) -> dict[tuple[int, int], str]:
    """Возвращает словарь (module_pos, lesson_pos) → lesson_title."""
    structure_file = course_root / "structure.json"
    if not structure_file.exists():
        return {}
    data = json.loads(structure_file.read_text(encoding="utf-8"))
    titles: dict[tuple[int, int], str] = {}
    for section in data.get("structure", []):
        m = section.get("section_position", 0)
        for lesson in section.get("lessons", []):
            titles[(m, lesson.get("position", 0))] = lesson.get("lesson_title", "")
    return titles


def load_section_titles(course_root: Path) -> dict[int, str]:
    """Возвращает словарь module_pos → section_title."""
    structure_file = course_root / "structure.json"
    if not structure_file.exists():
        return {}
    data = json.loads(structure_file.read_text(encoding="utf-8"))
    return {
        s.get("section_position", 0): s.get("section_title", "")
        for s in data.get("structure", [])
    }


# ---------------------------------------------------------------------------
# Парсинг шагов урока
# ---------------------------------------------------------------------------

def parse_step(entry: dict, lesson_dir: Path) -> dict:
    """Читает данные шага и возвращает унифицированный словарь."""
    pos = entry["position"]
    step_type = entry["type"]
    folder = lesson_dir / f"{pos:02d}_{step_type}"

    result = {
        "position": pos,
        "type": step_type,
        "title": "",
        "summary": "",
        "raw_text": "",  # для TF-IDF
        "review_type": None,  # "peer" | "instructor" | None
    }

    if step_type in TEXT_TYPES:
        step_text = read_step_text(folder)
        result["title"] = title_from_html(step_text, fallback=f"Шаг {pos}")
        paragraphs = [p for p in extract_paragraphs(step_text) if len(p) > 20]
        result["summary"] = first_sentences(paragraphs[0]) if paragraphs else ""
        result["raw_text"] = clean_html(strip_code_blocks(step_text))

    elif step_type in TASK_TYPES:
        step_text = read_step_text(folder)
        result["title"] = title_from_html(step_text, fallback=f"Задача {pos}")
        result["raw_text"] = clean_html(strip_code_blocks(step_text))

    elif step_type in REVIEW_TYPES:
        step_text = read_step_text(folder)
        result["title"] = title_from_html(step_text, fallback=f"Задание {pos}")
        result["raw_text"] = clean_html(strip_code_blocks(step_text))
        # Тип ревью берём из lesson.json entry
        result["review_type"] = entry.get("instruction_type")

    elif step_type in QUIZ_TYPES:
        step_json_path = folder / "step.json"
        if step_json_path.exists():
            data = json.loads(step_json_path.read_text(encoding="utf-8"))
            question_html = data.get("text", "")
            result["title"] = title_from_json_text(question_html, fallback=f"Вопрос {pos}")

    return result


# ---------------------------------------------------------------------------
# Сборка summary
# ---------------------------------------------------------------------------

def build_summary(
    course: str,
    lesson_dir: Path,
    course_root: Path,
    idf: dict[str, float],
    lesson_titles: dict[tuple[int, int], str],
    section_titles: dict[int, str],
) -> tuple[Path, str]:
    lesson_json_path = lesson_dir / "lesson.json"
    if not lesson_json_path.exists():
        return lesson_dir / "summary.md", ""

    manifest = json.loads(lesson_json_path.read_text(encoding="utf-8"))
    entries = manifest.get("steps", [])

    relative = lesson_dir.relative_to(course_root)
    module_name = relative.parts[0] if len(relative.parts) > 0 else ""
    lesson_name = relative.parts[1] if len(relative.parts) > 1 else lesson_dir.name

    m_mod = LEADING_INDEX_RE.match(module_name)
    m_les = LEADING_INDEX_RE.match(lesson_name)
    module_idx = int(m_mod.group(1)) if m_mod else 0
    lesson_idx = int(m_les.group(1)) if m_les else 0

    lesson_title = lesson_titles.get((module_idx, lesson_idx), "")
    section_title = section_titles.get(module_idx, "")

    output_filename = f"{module_idx:02d}-{lesson_idx:02d}_summary.md"
    output_path = lesson_dir / output_filename

    steps = [parse_step(e, lesson_dir) for e in entries]

    theory = [s for s in steps if s["type"] in TEXT_TYPES]
    tasks = [s for s in steps if s["type"] in TASK_TYPES]
    quizzes = [s for s in steps if s["type"] in QUIZ_TYPES]
    reviews = [s for s in steps if s["type"] in REVIEW_TYPES]

    # Цель урока — первые предложения первого текстового шага
    goal = ""
    for s in theory:
        if s["summary"]:
            goal = s["summary"]
            break
    if not goal:
        goal = "Урок формирует базовый контекст по теме модуля."

    # TF-IDF термины — из всех текстовых шагов урока
    all_texts = [s["raw_text"] for s in steps if s["raw_text"]]
    terms = top_terms(all_texts, idf, limit=10)

    lines = ["# Summary", ""]

    # --- Lesson ---
    lines += ["## Lesson", ""]
    lines.append(f"- Path: `courses/{course}/{relative.as_posix()}`")
    if lesson_title:
        lines.append(f"- Title: {lesson_title}")
    if section_title:
        lines.append(f"- Module: {section_title}")

    # --- Goal ---
    lines += ["", "## Goal", goal]

    # --- Theory ---
    if theory:
        lines += ["", "## Theory", ""]
        for s in theory:
            summary = f" — {s['summary']}" if s["summary"] else ""
            lines.append(f"{s['position']}. {s['title']}{summary}")

    # --- Tasks ---
    has_tasks = tasks or quizzes or reviews
    if has_tasks:
        lines += ["", "## Tasks", ""]
        for s in quizzes:
            lines.append(f"{s['position']}. [{s['type']}] {s['title']}")
        for s in tasks:
            lines.append(f"{s['position']}. [code] {s['title']}")
        for s in reviews:
            review_label = {
                "peer": "ревью студентами",
                "instructor": "ревью преподавателем",
            }.get(s["review_type"] or "", "свободный ответ")
            lines.append(f"{s['position']}. [free-answer / {review_label}] {s['title']}")

    # --- Introduced Terms ---
    lines += ["", "## Introduced Terms"]
    if terms:
        for t in terms:
            lines.append(f"- {t}")
    else:
        lines.append("- —")

    content = "\n".join(lines) + "\n"
    return output_path, content


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def find_lesson_dirs(course_root: Path) -> list[Path]:
    """Папки уроков — те, в которых есть lesson.json."""
    return sorted(p.parent for p in course_root.rglob("lesson.json"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--course", required=True, help="Course directory name inside courses/")
    parser.add_argument("--module", type=int, default=None, help="Module number (optional)")
    parser.add_argument("--lesson", type=int, default=None, help="Lesson number (optional, requires --module)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent.parent
    course_root = repo_root / "courses" / args.course

    if not course_root.exists():
        raise SystemExit(f"Course not found: {course_root}")

    print("Building TF-IDF index...")
    idf = build_course_idf(course_root)
    lesson_titles = load_lesson_titles(course_root)
    section_titles = load_section_titles(course_root)

    lesson_dirs = find_lesson_dirs(course_root)

    if args.module is not None:
        lesson_dirs = [
            d for d in lesson_dirs
            if (m := LEADING_INDEX_RE.match(d.parent.name)) and int(m.group(1)) == args.module
        ]
        if args.lesson is not None:
            lesson_dirs = [
                d for d in lesson_dirs
                if (l := LEADING_INDEX_RE.match(d.name)) and int(l.group(1)) == args.lesson
            ]

    total = len(lesson_dirs)
    created = 0

    for lesson_dir in lesson_dirs:
        output_path, content = build_summary(
            course=args.course,
            lesson_dir=lesson_dir,
            course_root=course_root,
            idf=idf,
            lesson_titles=lesson_titles,
            section_titles=section_titles,
        )
        if not content:
            continue
        output_path.write_text(content, encoding="utf-8")
        created += 1
        print(f"[{created}/{total}] {output_path.relative_to(repo_root)}")

    print(f"Done: {created} summaries created.")


if __name__ == "__main__":
    main()
