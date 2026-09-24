"""Загрузка структуры курса и вспомогательные методы для метаданных Latorn."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .api import LatornAPIError
from .html_utils import sanitize_filename
from .step_types import LOCAL_TYPE_BY_API

if TYPE_CHECKING:
    from ..config import EditorConfig
    from .api import LatornAPIClient


class CourseStructureMixin:
    if TYPE_CHECKING:
        config: EditorConfig
        client: LatornAPIClient
        SUPPORTED_STEP_TYPES: set[str]
        HTML_STEP_TYPES: set[str]
        course_id: str
        course_info: dict[str, Any]
        structure: list[dict[str, Any]]
        course_name: str
        total_lessons: int
        root_folder: Path

        def _load_course_data(self) -> bool: ...
        def _save_course_data(self) -> None: ...

    def _get_course_info(self) -> dict[str, Any] | None:
        """Загружает метаданные курса и подготавливает локальную корневую папку."""
        self.course_id = self.config.course_id
        if not self.config.course_id:
            print("❌ Не указан course_id (id или slug курса Latorn)")
            return None

        if not all(
            hasattr(self, attr) for attr in ("course_info", "structure", "course_name")
        ):
            if self._load_course_data():
                print(f"Структура курса {self.course_id} загружена из structure.json")
            else:
                print(f"Загрузка структуры курса {self.course_id}...")
                self.course_info = self.client.get_course(self.course_id)
                # course_id из конфига может быть slug — дальше работаем по id
                self.course_id = self.course_info.get("id", self.course_id)
                if not self.config.course_name:
                    self.course_name = sanitize_filename(
                        self.course_info.get("title", f"course_{self.course_id}")
                    )
                else:
                    self.course_name = self.config.course_name

                self.structure = self.get_course_structure()
                if self.structure:
                    self._save_course_data()

            if not self.structure:
                print("❌ Не удалось получить структуру курса.")
                return None
        else:
            print("Структура курса:")

        self.total_lessons = sum(len(section["lessons"]) for section in self.structure)
        print(f"Название курса: {self.course_name}")
        print(f"Модулей: {len(self.structure)}, Уроков: {self.total_lessons}")

        self.root_folder = Path(self.config.course_save_folder) / self.course_name
        self.root_folder.mkdir(parents=True, exist_ok=True)
        return self.course_info

    def sanitize_filename(self, name: str) -> str:
        """Сохраняем публичный метод для обратной совместимости."""
        return sanitize_filename(name)

    def get_course_structure(self) -> list[dict[str, Any]]:
        """Строит локальное представление структуры курса из sections/lessons.

        У Latorn уроки принадлежат модулю напрямую (без units, как было
        на Stepik), поэтому структура собирается двумя запросами-списками.
        """
        structure: list[dict[str, Any]] = []
        try:
            sections = self.client.list_sections(self.course_id)
        except LatornAPIError as exc:
            if exc.status_code != 404:
                raise
            # /api/sections не пускает команду курса к неопубликованному
            # черновику (курс виден, а его модули — нет). Аналитика курса
            # отдаёт полную структуру, поэтому строим её оттуда.
            print(
                "  ⚠️ /api/sections недоступен для этого курса (черновик команды) — "
                "строю структуру из аналитики"
            )
            return self._structure_from_analytics()

        if not sections:
            print("❌ Не удалось получить модули курса")
            return []

        for section_info in sections:
            section_id = section_info.get("id", "")
            section_title = section_info.get("title", f"Модуль {section_id}")
            section_position = section_info.get("position", 0)

            lessons: list[dict[str, Any]] = []
            for lesson_info in self.client.list_lessons(section_id):
                lessons.append(
                    {
                        "lesson_id": lesson_info.get("id", ""),
                        "lesson_title": lesson_info.get("title", ""),
                        "position": lesson_info.get("position", 0),
                        "lesson_info": lesson_info,
                    }
                )

            if not lessons:
                print(f"  ⚠️ Модуль '{section_title}' не содержит уроков")

            structure.append(
                {
                    "section_id": section_id,
                    "section_title": section_title,
                    "section_position": section_position,
                    "section_info": section_info,
                    "lessons": lessons,
                }
            )

        structure.sort(key=lambda section: section["section_position"])
        return structure

    def _structure_from_analytics(self) -> list[dict[str, Any]]:
        """Строит структуру курса из sectionStats/lessonStats аналитики.

        Запасной путь для черновиков, где /api/sections отвечает 404 команде
        курса. Ограничение: модули и уроки без шагов могут отсутствовать
        в аналитике. lesson_info добирается поштучно через /api/lessons/[id]
        (этот маршрут пускает команду и к черновику).
        """
        analytics = self.client.get_course_analytics(self.course_id)

        sections_by_id: dict[str, dict[str, Any]] = {}
        for section_stat in analytics.get("sectionStats", []):
            section_id = section_stat.get("sectionId", "")
            if not section_id:
                continue
            title = section_stat.get("sectionTitle", f"Модуль {section_id}")
            position = section_stat.get("sectionPosition", 0)
            sections_by_id[section_id] = {
                "section_id": section_id,
                "section_title": title,
                "section_position": position,
                "section_info": {
                    "id": section_id,
                    "courseId": self.course_id,
                    "title": title,
                    "position": position,
                },
                "lessons": [],
            }

        for lesson_stat in analytics.get("lessonStats", []):
            lesson_id = lesson_stat.get("lessonId", "")
            section_id = lesson_stat.get("sectionId", "")
            section = sections_by_id.get(section_id)
            if not lesson_id or section is None:
                continue
            try:
                lesson_info = self.client.get_lesson(lesson_id)
            except LatornAPIError:
                lesson_info = {
                    "id": lesson_id,
                    "sectionId": section_id,
                    "title": lesson_stat.get("lessonTitle", ""),
                    "position": lesson_stat.get("lessonPosition", 0),
                }
            section["lessons"].append(
                {
                    "lesson_id": lesson_id,
                    "lesson_title": lesson_info.get("title", lesson_stat.get("lessonTitle", "")),
                    "position": lesson_info.get("position", lesson_stat.get("lessonPosition", 0)),
                    "lesson_info": lesson_info,
                }
            )

        structure = list(sections_by_id.values())
        for section in structure:
            section["lessons"].sort(key=lambda l: l["position"])
        structure.sort(key=lambda s: s["section_position"])

        if not structure:
            print("❌ Аналитика не вернула структуру курса")
        return structure

    def get_section_info(self, section_id: str) -> dict[str, Any]:
        """Публичный прокси к API клиента для модуля."""
        return self.client.get_section(section_id)

    def get_lesson_title(self, lesson_id: str) -> str:
        """Возвращает заголовок урока или запасное имя при ошибке API."""
        try:
            lesson = self.client.get_lesson(lesson_id)
            return lesson.get("title", f"Урок {lesson_id}")
        except Exception:
            return f"Урок {lesson_id}"

    def get_lesson_steps(self, lesson_id: str) -> list[dict[str, Any]]:
        """Возвращает только поддерживаемые шаги урока (редакторский список)."""
        raw_steps = self.client.list_steps(lesson_id)
        lesson_steps = [
            step
            for step in raw_steps
            if LOCAL_TYPE_BY_API.get(step.get("type", "")) in self.SUPPORTED_STEP_TYPES
        ]
        lesson_steps.sort(key=lambda step: step.get("position", 0))
        return lesson_steps

    def get_theory(self, lesson_id: str) -> list[dict[str, Any]]:
        """Возвращает только HTML-теорию урока в старом совместимом формате."""
        theory_blocks = []
        for step in self.client.list_steps(lesson_id):
            if LOCAL_TYPE_BY_API.get(step.get("type", "")) in self.HTML_STEP_TYPES:
                html = (step.get("block") or {}).get("text", "").strip()
                if html:
                    theory_blocks.append(
                        {"position": step.get("position", 0), "html": html}
                    )
        theory_blocks.sort(key=lambda block: block["position"])
        return theory_blocks
