"""Выводит курсы, доступные текущему аккаунту Latorn (id, slug, название).

Запуск из корня проекта:
    .\\.venv\\Scripts\\python.exe scripts\\list_my_courses.py

Полезно, чтобы узнать id/slug черновика, которого нет в публичном каталоге.
Только читает данные, ничего не изменяет.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.latorn_downloader.api import LatornAPIClient


def main() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8")
        except Exception:
            pass

    client = LatornAPIClient.from_env()

    seen: dict[str, dict] = {}

    # Общий список: с сессией преподавателя может включать и черновики
    for course in client._collect_paginated("/api/courses", "courses"):
        seen[course["id"]] = course

    # Рабочий список преподавателя — пробуем известные фильтры
    for params in ({"filter": "instructor"}, {"instructor": "true"}, {"mine": "true"}):
        try:
            for course in client._collect_paginated("/api/courses", "courses", dict(params)):
                seen[course["id"]] = course
        except Exception:
            continue

    # Сводка кабинета преподавателя тоже может содержать курсы
    try:
        dashboard = client._get_json("/api/dashboard/instructor")
        for course in dashboard.get("courses", []):
            if isinstance(course, dict) and course.get("id"):
                seen.setdefault(course["id"], course)
    except Exception:
        pass

    if not seen:
        print("Курсы не найдены — проверь авторизацию в .env")
        return

    for course in sorted(seen.values(), key=lambda c: c.get("title", "")):
        published = "published" if course.get("isPublished") else "DRAFT"
        print(f"{course.get('id', '?')}  [{published}]")
        print(f"    slug:  {course.get('slug', '?')}")
        print(f"    title: {course.get('title', '?')}")


if __name__ == "__main__":
    main()
