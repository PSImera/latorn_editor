"""Сериализация шагов: Latorn API ↔ локальные файлы.

Каждый шаг — своя подпапка {pos:02d}_{type_safe}/.
Содержимое по типу:
  text          →  task.md | task.html  [+ step.json, если в block есть другие поля]
  code          →  task.md | task.html + step.json + precode.py + tests.py|tests/NN.py
  review        →  task.md | task.html + step.json (block без поля text)
  video         →  step.json (block целиком)
  тестовые      →  step.json (block целиком: choice, number, free-text,
                              sorting, matching)

Имя файла с текстом зависит от block.format: "markdown" → task.md,
"html" (или отсутствие поля) → task.html. Для новых локальных шагов
дефолт — markdown: достаточно создать task.md, uploader сам подставит
"format": "markdown" в block.

Схема block у Latorn своя для каждого типа шага, и сервер строго проверяет
её при публикации. Локальное хранение построено без потерь: всё, что пришло
в block, сохраняется и при загрузке собирается обратно один в один.

Код задач (`code`/`review`) выносится из step.json в отдельные читаемые
`.py`-файлы рядом с шагом:
  block.preCode              →  precode.py
  block.testSuites[0].code   →  tests.py           (один сьют)
  block.testSuites[i].code   →  tests/{i+1:02d}.py (несколько сьютов)
В step.json остаётся только метаданные сьютов (name/hidden/…) без кода.
При загрузке код подставляется обратно из файлов; если файла нет — берётся
инлайн-значение из step.json (обратная совместимость со старым форматом).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from .html_format import format_step_html
from .lesson_manifest import step_folder
from .step_types import (
    CODE_STEP_TYPES,
    LOCAL_TYPE_BY_API,
    REVIEW_STEP_TYPES,
    SUPPORTED_STEP_TYPES,
    TEXT_STEP_TYPES,
)

# Типы, у которых условие хранится в task.md / task.html
_TEXT_TASK_TYPES = TEXT_STEP_TYPES | CODE_STEP_TYPES | REVIEW_STEP_TYPES

_TASK_MD = "task.md"
_TASK_HTML = "task.html"

# Вынесенный из step.json код задач
_PRECODE = "precode.py"
_TESTS = "tests.py"
_TESTS_DIR = "tests"


# ---------------------------------------------------------------------------
# Download: API → local files
# ---------------------------------------------------------------------------

def save_step(
    step: dict[str, Any],
    lesson_folder: Path,
    media_downloader: "Callable[[str, Path], None] | None" = None,
) -> dict[str, Any]:
    """Сохраняет шаг локально; возвращает запись для lesson.json."""
    api_type = step.get("type", "")
    step_type = LOCAL_TYPE_BY_API.get(api_type, "")
    position = step.get("position", 0)

    if step_type not in SUPPORTED_STEP_TYPES:
        raise ValueError(f"Неизвестный тип шага: {api_type!r}")

    folder = step_folder(lesson_folder, position, step_type)
    folder.mkdir(parents=True, exist_ok=True)

    block = step.get("block") or {}
    entry: dict[str, Any] = {
        "position": position,
        "step_id": step.get("id"),
        "type": step_type,
    }

    if step_type in _TEXT_TASK_TYPES:
        text = block.get("text", "")
        task_name = _TASK_MD if block.get("format") == "markdown" else _TASK_HTML
        stale = folder / (_TASK_HTML if task_name == _TASK_MD else _TASK_MD)
        if stale.exists():
            stale.unlink()
        _write_task_text(folder / task_name, text, folder, media_downloader)
        rest = {k: v for k, v in block.items() if k != "text"}
        rest = _externalize_code(folder, rest)
        json_path = folder / "step.json"
        if rest or step_type not in TEXT_STEP_TYPES:
            _write_json(json_path, rest)
        elif json_path.exists():
            json_path.unlink()
    else:
        if media_downloader is not None:
            media_downloader(json.dumps(block, ensure_ascii=False), folder)
        _write_json(folder / "step.json", block)

    return entry


def delete_step_folder(lesson_folder: Path, position: int, step_type: str) -> None:
    """Удаляет подпапку шага если она существует."""
    folder = step_folder(lesson_folder, position, step_type)
    if folder.exists():
        shutil.rmtree(folder)


# ---------------------------------------------------------------------------
# Upload: local files → Latorn block
# ---------------------------------------------------------------------------

def load_step(
    manifest_entry: dict[str, Any],
    lesson_folder: Path,
) -> tuple[dict[str, Any], str]:
    """Читает локальные файлы шага; возвращает (block, label)."""
    step_type = manifest_entry["type"]
    position = manifest_entry["position"]
    folder = step_folder(lesson_folder, position, step_type)

    if step_type not in SUPPORTED_STEP_TYPES:
        raise ValueError(f"Неизвестный тип шага: {step_type!r}")

    if step_type in _TEXT_TASK_TYPES:
        task_path = _find_task_file(folder)
        text = _strip_comments(_read_task_text(task_path))
        block = _read_json(folder / "step.json")
        if "format" not in block and task_path.name == _TASK_MD:
            block["format"] = "markdown"
        block["text"] = text
        block = _internalize_code(folder, block)
        return block, _title(text)

    block = _read_json(folder / "step.json")
    label = _title(str(block.get("text", "") or block.get("title", "")))
    return block, f"{step_type}: {label}" if label else step_type


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _find_task_file(folder: Path) -> Path:
    """Файл с текстом шага: task.md, если он есть, иначе task.html."""
    md_path = folder / _TASK_MD
    if md_path.exists():
        return md_path
    return folder / _TASK_HTML


def _write_task_text(
    path: Path,
    content: str,
    folder: "Path | None" = None,
    media_downloader: "Callable[[str, Path], None] | None" = None,
) -> None:
    # HTML раскладываем по строкам: редактор Latorn отдаёт его одной строкой,
    # читать и править такое невозможно. Markdown пишем как есть.
    text = format_step_html(content) if path.name == _TASK_HTML else content.strip()
    path.write_text(text, encoding="utf-8")
    if media_downloader is not None and folder is not None:
        media_downloader(content, folder)


def save_task_text(folder: Path, text: str) -> Path:
    """Перезаписывает текст шага (task.md/task.html) в том же виде, что пишет даунлодер.

    Нужно аплоудеру: после загрузки картинок на Latorn локальные ссылки
    заменяются на адреса платформы, и файл должен уехать в репозиторий уже
    с ними — иначе следующая выгрузка загрузит те же файлы заново.
    """
    path = _find_task_file(folder)
    _write_task_text(path, text)
    return path


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")


# ---------------------------------------------------------------------------
# Код задач: step.json ↔ отдельные .py-файлы
# ---------------------------------------------------------------------------

def _write_code(path: Path, code: str) -> None:
    """Пишет код в .py: UTF-8 без BOM, платформенные переводы строк, без хвостового \\n."""
    path.write_text((code or "").rstrip("\n"), encoding="utf-8")


def _read_code(path: Path) -> str:
    """Читает .py в вид, идентичный полю block (\\n внутри, без хвостового \\n)."""
    return path.read_text(encoding="utf-8").rstrip("\n")


def _test_files(folder: Path) -> list[Path]:
    """Файлы сьютов в порядке следования: tests/NN.py или одиночный tests.py."""
    tests_dir = folder / _TESTS_DIR
    if tests_dir.is_dir():
        return sorted(tests_dir.glob("*.py"))
    single = folder / _TESTS
    return [single] if single.exists() else []


def _clear_test_files(folder: Path) -> None:
    (folder / _TESTS).unlink(missing_ok=True)
    tests_dir = folder / _TESTS_DIR
    if tests_dir.is_dir():
        shutil.rmtree(tests_dir)


def _externalize_code(folder: Path, rest: dict[str, Any]) -> dict[str, Any]:
    """Выносит preCode и код сьютов в .py-файлы; в rest остаются метаданные без кода."""
    (folder / _PRECODE).unlink(missing_ok=True)
    if "preCode" in rest:
        _write_code(folder / _PRECODE, rest.pop("preCode") or "")

    _clear_test_files(folder)
    suites = rest.get("testSuites")
    if isinstance(suites, list) and suites:
        if len(suites) == 1:
            _write_code(folder / _TESTS, suites[0].get("code", ""))
        else:
            tests_dir = folder / _TESTS_DIR
            tests_dir.mkdir(exist_ok=True)
            for i, suite in enumerate(suites, 1):
                _write_code(tests_dir / f"{i:02d}.py", suite.get("code", ""))
        rest["testSuites"] = [
            {k: v for k, v in suite.items() if k != "code"} for suite in suites
        ]
    return rest


def _internalize_code(folder: Path, block: dict[str, Any]) -> dict[str, Any]:
    """Подставляет preCode и код сьютов из .py-файлов; фолбэк — инлайн-значения step.json."""
    precode_path = folder / _PRECODE
    if precode_path.exists():
        block["preCode"] = _read_code(precode_path)

    suites = block.get("testSuites")
    files = _test_files(folder)
    if isinstance(suites, list) and suites and files:
        if len(files) != len(suites):
            raise ValueError(
                f"{folder}: сьютов в step.json — {len(suites)}, файлов тестов — {len(files)}"
            )
        for suite, path in zip(suites, files):
            suite["code"] = _read_code(path)
    return block


def _read_task_text(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    body = re.search(r"<body[^>]*>(.*?)</body>", text, re.IGNORECASE | re.DOTALL)
    return body.group(1).strip() if body else text


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _strip_comments(html: str) -> str:
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL).strip()


def _title(text: str, max_length: int = 80) -> str:
    m = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", text, re.IGNORECASE | re.DOTALL)
    if m is None:
        m = re.search(r"^#{1,3}\s+(.+)$", text, re.MULTILINE)
    title = re.sub(r"<[^>]+>", "", (m.group(1) if m else text)).strip()
    return title[:max_length]
