r"""Собирает бриф для субагента-студента: теория урока плюс вопросы без ответов.

Запуск:
    .\.venv\Scripts\python.exe scripts\build_student_brief.py courses\computer-vision\08\02 <выходной файл>

В бриф идут только тексты теоретических шагов и формулировки вопросов с вариантами.
Верные ответы, подсказки и порядок элементов сортировки в бриф не попадают:
варианты choice нумеруются как их видит студент, элементы sorting и правая колонка
matching перемешиваются детерминированным зерном.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path


def load_steps(lesson_dir: Path) -> list[dict[str, object]]:
    manifest = json.loads((lesson_dir / "lesson.json").read_text(encoding="utf-8"))
    steps: list[dict[str, object]] = []
    for entry in manifest["steps"]:
        position = int(entry["position"])
        step_type = str(entry["type"])
        folder = lesson_dir / f"{position:02d}_{step_type.replace('-', '_')}"
        steps.append({"position": position, "type": step_type, "folder": folder})
    return steps


def theory_text(folder: Path) -> str | None:
    for name in ("task.html", "task.md"):
        path = folder / name
        if path.exists():
            return path.read_text(encoding="utf-8")
    return None


def render_question(number: int, step_type: str, block: dict[str, object]) -> str:
    lines = [f"### Вопрос {number} ({step_type})", "", str(block.get("text", "")).strip(), ""]
    rng = random.Random(number)

    if step_type == "choice":
        options = list(block.get("options", []))  # type: ignore[arg-type]
        if block.get("isMultiple"):
            lines.append("_Верных ответов несколько._")
            lines.append("")
        for index, option in enumerate(options, start=1):
            lines.append(f"{index}. {option['text']}")
    elif step_type == "sorting":
        items = list(block.get("items", []))  # type: ignore[arg-type]
        rng.shuffle(items)
        for index, item in enumerate(items, start=1):
            lines.append(f"{index}. {item['text']}")
    elif step_type == "matching":
        left = list(block.get("leftItems", []))  # type: ignore[arg-type]
        right = list(block.get("rightItems", []))  # type: ignore[arg-type]
        rng.shuffle(right)
        lines.append("Левая колонка:")
        for index, item in enumerate(left, start=1):
            lines.append(f"{index}. {item['text']}")
        lines.append("")
        lines.append("Правая колонка:")
        for letter, item in zip("АБВГДЕЖЗ", right):
            lines.append(f"{letter}. {item['text']}")
    elif step_type == "number":
        lines.append("_Ответ — число._")
    elif step_type == "free-text":
        lines.append("_Ответ — короткая строка._")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1

    lesson_dir = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    parts = ["# Материалы урока", ""]
    questions: list[str] = []
    number = 0

    for step in load_steps(lesson_dir):
        folder = step["folder"]
        assert isinstance(folder, Path)
        step_type = str(step["type"])
        if step_type in {"text", "video", "code", "review"}:
            text = theory_text(folder)
            if text is not None:
                parts.append(f"## Шаг {step['position']}")
                parts.append("")
                parts.append(text)
                parts.append("")
            continue

        block_path = folder / "step.json"
        if not block_path.exists():
            continue
        block = json.loads(block_path.read_text(encoding="utf-8"))
        number += 1
        questions.append(render_question(number, step_type, block))

    parts.append("# Вопросы теста")
    parts.append("")
    parts.extend(questions)

    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"{out_path} — вопросов: {number}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())