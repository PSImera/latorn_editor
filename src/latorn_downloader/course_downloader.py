"""Фасад для сценариев скачивания и загрузки курсов Latorn."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any
import warnings

try:
    from src.config import EditorConfig
except ImportError:  # pragma: no cover - fallback для прямого запуска из папки src
    from config import EditorConfig

from .api import LatornAPIClient
from .course_structure import CourseStructureMixin
from .download_workflow import DownloadWorkflowMixin
from .local_steps import LocalStepsMixin
from .step_types import (
    COMPLEX_STEP_TYPES,
    HTML_STEP_TYPES,
    MAX_LESSON_STEPS,
    TEST_TASK_STEP_TYPES,
    SUPPORTED_STEP_TYPES,
    TEXT_STEP_TYPES,
)
from .structure_upload_workflow import StructureUploadMixin
from .upload_workflow import UploadWorkflowMixin

warnings.filterwarnings("ignore")


class CourseDownloader(
    CourseStructureMixin,
    DownloadWorkflowMixin,
    UploadWorkflowMixin,
    StructureUploadMixin,
    LocalStepsMixin,
):
    """Скачивает и загружает материалы курса Latorn."""

    TEXT_STEP_TYPES = TEXT_STEP_TYPES
    COMPLEX_STEP_TYPES = COMPLEX_STEP_TYPES
    TEST_TASK_STEP_TYPES = TEST_TASK_STEP_TYPES
    HTML_STEP_TYPES = HTML_STEP_TYPES
    SUPPORTED_STEP_TYPES = SUPPORTED_STEP_TYPES
    MAX_LESSON_STEPS = MAX_LESSON_STEPS

    def __init__(
        self,
        config: EditorConfig | None = None,
        api_client: LatornAPIClient | None = None,
    ):
        self.config = config or EditorConfig()
        self.client = api_client or LatornAPIClient.from_env()
        self._get_course_info()

    def _get_store_path(self) -> Path:
        course_name = getattr(self, "course_name", None) or self.config.course_name
        return Path(self.config.course_save_folder) / course_name / "structure.json"

    def _load_course_data(self) -> bool:
        store_path = self._get_store_path()
        if not store_path.exists():
            return False

        try:
            data = json.loads(store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"⚠️ Не удалось прочитать structure.json: {exc}")
            return False

        stored_id = data.get("course_id")
        # в конфиге может быть slug, в файле — id (или наоборот)
        stored_slug = data.get("course_info", {}).get("slug")
        if self.course_id not in (stored_id, stored_slug):
            return False

        self.course_info = data.get("course_info", {})
        self.course_id = stored_id or self.course_id
        self.course_name = data.get("course_name") or self.config.course_name
        self.structure = data.get("structure", [])
        return bool(self.structure)

    def _save_course_data(self) -> None:
        store_path = self._get_store_path()
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(
            json.dumps(
                {
                    "course_id": self.course_id,
                    "course_name": self.course_name,
                    "course_info": self.course_info,
                    "structure": self.structure,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def refresh_structure(self, *, pull_course_info: bool = False) -> None:
        """Обновляет структуру курса из Latorn (pull: удалённое состояние побеждает).

        - Названия и позиции модулей/уроков берутся с Latorn; локальные значения,
          изменённые но не загруженные uploader'ом, перезаписываются с уведомлением.
        - Черновики (записи без section_id/lesson_id) сохраняются как есть.
        - course_info (описание курса) по умолчанию остаётся локальным: локальный
          файл — источник для uploader'а. Расхождения с Latorn выводятся как
          предупреждения, но не перезаписываются.
        - pull_course_info=True — забрать описание курса с Latorn поверх
          локального. Нужно, когда описание правили в веб-редакторе на сайте:
          без этого флага скачивание его не подхватит, а следующая выгрузка
          затрёт правки локальным текстом.
        - Локальные папки модулей/уроков переименовываются вслед за сменой позиций.
        """
        store_path = self._get_store_path()
        local_course_info: dict = {}
        local_structure: list = []
        if store_path.exists():
            try:
                stored = json.loads(store_path.read_text(encoding="utf-8"))
                local_course_info = stored.get("course_info", {})
                local_structure = stored.get("structure", [])
            except (OSError, json.JSONDecodeError):
                pass

        print(f"Обновление структуры курса {self.course_id} с Latorn...")
        fresh_course = self.client.get_course(self.course_id)
        latorn_structure = self.get_course_structure()

        self.structure = self._merge_structures(local_structure, latorn_structure)

        if local_course_info and not pull_course_info:
            self.course_info = local_course_info
            self._warn_course_metadata_diffs(fresh_course)
        else:
            self.course_info = self._pull_course_info(fresh_course, local_course_info)

        self._sync_local_folders(local_structure, self.structure)
        self._save_course_data()
        total = sum(len(s["lessons"]) for s in self.structure)
        print(f"Готово. Модулей: {len(self.structure)}, уроков: {total}")

    def _pull_course_info(
        self, fresh_course: dict[str, Any], local_course_info: dict[str, Any]
    ) -> dict[str, Any]:
        """Берёт описание курса с Latorn поверх локального.

        Переносятся только поля описания (те же, что отправляет uploader) плюс
        `coverImage`. Технические поля локального `course_info` — `slug`, `price`,
        счётчики — остаются как были: `slug` на платформе меняется при
        переименовании курса, а по нему курс ищется в `course_config.json`,
        и подмена его на свежий рвёт следующий запуск даунлодера.

        `summary` раскладывается по строкам той же функцией, что и текст шагов:
        веб-редактор отдаёт его одной строкой и с дописанным пустым абзацем
        в конце, читать и править такое в structure.json невозможно.
        """
        from .html_format import format_step_html
        from .structure_upload_workflow import _COURSE_DESCRIPTION_FIELDS

        if not local_course_info:
            return fresh_course

        pulled = dict(local_course_info)
        changed: list[str] = []
        for field in (*_COURSE_DESCRIPTION_FIELDS, "coverImage"):
            if field not in fresh_course:
                continue
            value = fresh_course[field]
            if field == "summary" and isinstance(value, str) and value:
                value = format_step_html(value)
            if pulled.get(field) != value:
                pulled[field] = value
                changed.append(field)

        if changed:
            print(f"  Описание курса взято с Latorn: {', '.join(changed)}")
        else:
            print("  Описание курса: расхождений с Latorn нет")
        return pulled

    def _warn_course_metadata_diffs(self, fresh_course: dict[str, Any]) -> None:
        """Предупреждает, если локальные поля описания курса расходятся с Latorn.

        Описание курса не перезаписывается при refresh (локальный файл — источник
        для uploader'а), но пользователь должен видеть, что uploader при следующем
        запуске перезапишет серверные значения.
        """
        from .structure_upload_workflow import _COURSE_DESCRIPTION_FIELDS

        diffs = [
            field
            for field in _COURSE_DESCRIPTION_FIELDS
            if field in self.course_info
            and self.course_info.get(field) != fresh_course.get(field)
        ]
        if diffs:
            print(
                "  ⚠️ Поля описания курса расходятся с Latorn (uploader перезапишет "
                f"серверные значения): {', '.join(diffs)}"
            )

    def _sync_local_folders(
        self,
        old_structure: list[dict],
        new_structure: list[dict],
    ) -> None:
        """Переименовывает локальные папки модулей и уроков вслед за сменой позиций.

        Сравнивает расположение сущностей (по section_id/lesson_id) до и после
        мержа. Переименования двухфазные (через временные имена), чтобы обмен
        позициями двух папок не приводил к конфликтам. Папки, которых нет на
        диске, пропускаются.
        """
        root = self.root_folder

        old_sec_pos: dict[str, int] = {
            s["section_id"]: s["section_position"]
            for s in old_structure
            if s.get("section_id")
        }
        new_sec_pos: dict[str, int] = {
            s["section_id"]: s["section_position"]
            for s in new_structure
            if s.get("section_id")
        }

        # --- модули ---------------------------------------------------------
        sec_moves = [
            (sid, old_sec_pos[sid], new_sec_pos[sid])
            for sid in old_sec_pos
            if sid in new_sec_pos and old_sec_pos[sid] != new_sec_pos[sid]
        ]
        sec_tmp: dict[str, Path] = {}
        for sid, old_pos, _ in sec_moves:
            src = root / f"{old_pos:02d}"
            if src.exists():
                tmp = root / f"_tmp_section_{sid}"
                src.rename(tmp)
                sec_tmp[sid] = tmp
        for sid, old_pos, new_pos in sec_moves:
            tmp = sec_tmp.get(sid)
            if tmp is None:
                continue
            dst = root / f"{new_pos:02d}"
            if dst.exists():
                print(f"  ⚠️ Папка {dst} уже занята — модуль {sid} оставлен в {tmp}")
                continue
            tmp.rename(dst)
            print(f"  Папка модуля {old_pos:02d}/ → {new_pos:02d}/")

        def lesson_locations(structure: list[dict]) -> dict[str, tuple[str, int]]:
            locations: dict[str, tuple[str, int]] = {}
            for sec in structure:
                section_id = sec.get("section_id") or ""
                if not section_id:
                    continue
                for lesson in sec.get("lessons", []):
                    if lesson.get("lesson_id"):
                        locations[lesson["lesson_id"]] = (section_id, lesson["position"])
            return locations

        old_loc = lesson_locations(old_structure)
        new_loc = lesson_locations(new_structure)

        # --- уроки ----------------------------------------------------------
        lesson_tmp: dict[str, Path] = {}
        for lesson_id, (old_sid, old_lpos) in old_loc.items():
            if lesson_id not in new_loc or old_loc[lesson_id] == new_loc[lesson_id]:
                continue
            # папка старого модуля уже могла переехать — берём её итоговую позицию
            cur_sec_pos = new_sec_pos.get(old_sid, old_sec_pos[old_sid])
            src = root / f"{cur_sec_pos:02d}" / f"{old_lpos:02d}"
            if src.exists():
                tmp = root / f"_tmp_lesson_{lesson_id}"
                src.rename(tmp)
                lesson_tmp[lesson_id] = tmp
        for lesson_id, tmp in lesson_tmp.items():
            new_sid, new_lpos = new_loc[lesson_id]
            sec_pos = new_sec_pos[new_sid]
            dst_dir = root / f"{sec_pos:02d}"
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / f"{new_lpos:02d}"
            if dst.exists():
                print(f"  ⚠️ Папка {dst} уже занята — урок {lesson_id} оставлен в {tmp}")
                continue
            tmp.rename(dst)
            old_sid, old_lpos = old_loc[lesson_id]
            old_spos = old_sec_pos.get(old_sid, sec_pos)
            print(f"  Папка урока {old_spos:02d}/{old_lpos:02d}/ → {sec_pos:02d}/{new_lpos:02d}/")
            self._fix_summary_names(dst, sec_pos, new_lpos)

        # после переезда модуля имена саммари внутри его уроков тоже устаревают
        for sid, _, new_pos in sec_moves:
            if sid not in sec_tmp:
                continue
            module_dir = root / f"{new_pos:02d}"
            if not module_dir.exists():
                continue
            for lesson_dir in module_dir.iterdir():
                if lesson_dir.is_dir() and re.fullmatch(r"\d{2}", lesson_dir.name):
                    self._fix_summary_names(lesson_dir, new_pos, int(lesson_dir.name))

    @staticmethod
    def _fix_summary_names(lesson_dir: Path, sec_pos: int, lesson_pos: int) -> None:
        """Приводит имена файлов саммари `MM-LL_summary.md` к новой позиции урока."""
        target_name = f"{sec_pos:02d}-{lesson_pos:02d}_summary.md"
        for f in lesson_dir.glob("*_summary.md"):
            if re.fullmatch(r"\d{2}-\d{2}_summary\.md", f.name) and f.name != target_name:
                f.rename(lesson_dir / target_name)
                print(f"    Саммари {f.name} → {target_name}")

    @staticmethod
    def _merge_structures(
        local: list[dict],
        latorn: list[dict],
    ) -> list[dict]:
        """Pull-мерж локальной структуры со свежей с Latorn.

        Правила:
        - Модули матчатся по section_id; названия и позиции берутся с Latorn.
        - Уроки матчатся по lesson_id; названия, позиции и lesson_info
          берутся с Latorn.
        - Локальные черновики (записи без lesson_id/section_id) сохраняются.
        - Сущности, которые есть на Latorn но отсутствуют локально, добавляются.
        - Локальные сущности с id, которых больше нет на Latorn, сохраняются
          с предупреждением (refresh ничего не удаляет).
        """
        remote_lesson_ids: set[str] = {
            lesson["lesson_id"]
            for sec in latorn
            for lesson in sec.get("lessons", [])
            if lesson.get("lesson_id")
        }
        latorn_section_ids: set[str] = {s["section_id"] for s in latorn}
        local_by_id: dict[str, dict] = {
            s["section_id"]: s for s in local if s.get("section_id")
        }

        merged_sections: list[dict] = []
        for latorn_sec in latorn:
            local_sec = local_by_id.get(latorn_sec["section_id"])
            if local_sec is None:
                # модуль есть на Latorn, но не локально — добавляем
                merged_sections.append(latorn_sec)
                continue

            if local_sec["section_title"] != latorn_sec["section_title"]:
                print(
                    f"  Модуль: название обновлено с Latorn: "
                    f"«{local_sec['section_title']}» → «{latorn_sec['section_title']}»"
                )
            if local_sec["section_position"] != latorn_sec["section_position"]:
                print(
                    f"  Модуль «{latorn_sec['section_title']}»: позиция "
                    f"{local_sec['section_position']} → {latorn_sec['section_position']}"
                )

            merged_sec = {
                **local_sec,
                "section_id": latorn_sec["section_id"],
                "section_title": latorn_sec["section_title"],
                "section_position": latorn_sec["section_position"],
                "section_info": latorn_sec["section_info"],
            }
            merged_sec["lessons"] = CourseDownloader._merge_lessons(
                local_sec.get("lessons", []),
                latorn_sec.get("lessons", []),
                remote_lesson_ids,
            )
            merged_sections.append(merged_sec)

        for local_sec in local:
            section_id = local_sec.get("section_id") or ""
            if section_id and section_id not in latorn_section_ids:
                print(
                    f"  ⚠️ Модуль «{local_sec['section_title']}» (id={section_id}) "
                    "не найден на Latorn — оставлен локально"
                )
                merged_sections.append(local_sec)
            elif not section_id:
                # черновик модуля — оставляем как есть
                merged_sections.append(local_sec)

        merged_sections.sort(key=lambda s: s["section_position"])
        return merged_sections

    @staticmethod
    def _merge_lessons(
        local: list[dict],
        latorn: list[dict],
        remote_lesson_ids: set[str],
    ) -> list[dict]:
        latorn_by_lesson_id: dict[str, dict] = {
            l["lesson_id"]: l for l in latorn if l["lesson_id"]
        }
        matched_ids: set[str] = set()

        merged: list[dict] = []
        for lesson in local:
            lesson_id = lesson.get("lesson_id") or ""
            if not lesson_id:
                # черновик — оставляем как есть
                merged.append(lesson)
                continue
            latorn_lesson = latorn_by_lesson_id.get(lesson_id)
            if latorn_lesson is None:
                if lesson_id in remote_lesson_ids:
                    # урок переехал в другой модуль на Latorn — появится там
                    print(
                        f"  Урок «{lesson['lesson_title']}» перенесён "
                        "в другой модуль на Latorn"
                    )
                    continue
                print(
                    f"  ⚠️ Урок «{lesson['lesson_title']}» (id={lesson_id}) "
                    "не найден на Latorn — оставлен локально"
                )
                merged.append(lesson)
                continue

            matched_ids.add(lesson_id)
            if lesson["lesson_title"] != latorn_lesson["lesson_title"]:
                print(
                    f"  Урок: название обновлено с Latorn: "
                    f"«{lesson['lesson_title']}» → «{latorn_lesson['lesson_title']}»"
                )
            if lesson["position"] != latorn_lesson["position"]:
                print(
                    f"  Урок «{latorn_lesson['lesson_title']}»: позиция "
                    f"{lesson['position']} → {latorn_lesson['position']}"
                )
            merged.append({
                **lesson,
                "lesson_title": latorn_lesson["lesson_title"],
                "position": latorn_lesson["position"],
                "lesson_info": latorn_lesson["lesson_info"],
            })

        # уроки, которые есть на Latorn но не локально — добавляем
        for latorn_lesson in latorn:
            if latorn_lesson["lesson_id"] and latorn_lesson["lesson_id"] not in matched_ids:
                merged.append(latorn_lesson)

        merged.sort(key=lambda l: l["position"])
        return merged

    def delete_store(self) -> None:
        store_path = self._get_store_path()
        if store_path.exists():
            store_path.unlink()
            print(f"structure.json удалён: {store_path}")
        else:
            print(f"structure.json не найден: {store_path}")
