"""Минимальная CLI-обёртка вокруг `CourseDownloader`.

Скачивает курс по умолчанию (`default_course` из course_config.json)
целиком, без аргументов. Для выборочного скачивания используй
`downloader.py` в корне проекта.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

try:
    from src.config import EditorConfig
except ImportError:  # pragma: no cover - fallback для прямого запуска из папки src
    from config import EditorConfig

from .course_downloader import CourseDownloader


def main() -> None:
    """Запускает скачивание курса по умолчанию из course_config.json."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8")
        except Exception:
            pass

    defaults = json.loads(Path("course_config.json").read_text(encoding="utf-8"))
    course_name = defaults.get("default_course", "")
    course_id = defaults.get("courses", {}).get(course_name, "")

    config = EditorConfig(course_id=course_id, course_name=course_name)
    downloader = CourseDownloader(config)
    downloader.download_course()
