"""Манифест урока lesson.json — единственный источник истины о шагах урока.

Структура:
{
  "lesson_id": "cmo0mmhae038dr8nbfs1lrqvy",
  "steps": [
    {"position": 1, "step_id": "cmo0mmhaf038er8nb...", "type": "text"},
    {"position": 2, "step_id": "cmo0mmhag038fr8nb...", "type": "choice"},
    {"position": 3, "step_id": null,                   "type": "code"}
  ]
}

step_id = null (или отсутствует) — локальный черновик, шаг ещё не создан
на Latorn; uploader создаст его и запишет id.

Каждый шаг хранится в подпапке: {pos:02d}_{type_safe}/
  type_safe = type.replace("-", "_")  (напр. "free_text")

Содержимое подпапки по типу:
  text          →  task.md | task.html  [+ step.json при дополнительных полях block]
  code, review  →  task.md | task.html + step.json
  video         →  step.json
  остальные     →  step.json  (тестовые задачи: choice, number, free-text,
                               sorting, matching)

Имя файла с текстом зависит от block.format: "markdown" → task.md (дефолт
для новых шагов), "html" → task.html (курсы, импортированные со Stepik).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MANIFEST_FILENAME = "lesson.json"


def step_folder(lesson_folder: Path, position: int, step_type: str) -> Path:
    """Подпапка шага: {pos:02d}_{type_safe}."""
    type_safe = step_type.replace("-", "_")
    return lesson_folder / f"{position:02d}_{type_safe}"


def load_manifest(lesson_folder: Path) -> dict[str, Any]:
    path = lesson_folder / MANIFEST_FILENAME
    if not path.exists():
        return {"lesson_id": None, "steps": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(lesson_folder: Path, manifest: dict[str, Any]) -> None:
    manifest = dict(manifest)
    manifest["steps"] = sorted(manifest.get("steps", []), key=lambda s: s["position"])
    path = lesson_folder / MANIFEST_FILENAME
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=4), encoding="utf-8")


def upsert_step(manifest: dict[str, Any], step_entry: dict[str, Any]) -> None:
    steps: list[dict[str, Any]] = manifest.setdefault("steps", [])
    position = step_entry["position"]
    for i, existing in enumerate(steps):
        if existing["position"] == position:
            steps[i] = step_entry
            return
    steps.append(step_entry)


def get_step_entry(manifest: dict[str, Any], position: int) -> dict[str, Any] | None:
    for step in manifest.get("steps", []):
        if step["position"] == position:
            return step
    return None
