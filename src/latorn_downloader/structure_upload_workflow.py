"""Сценарий загрузки структуры курса (модули, уроки, описание) в Latorn."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .api import LatornAPIError

if TYPE_CHECKING:
    from .api import LatornAPIClient


def _confirm(prompt: str) -> bool:
    """Запрашивает Y/n в консоли. Возвращает True для Y/y/Enter, False для n/N."""
    while True:
        answer = input(f"{prompt} [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please enter Y or n.")


# Поля описания курса, которые синхронизируются с Latorn (camelCase — как в API)
_COURSE_DESCRIPTION_FIELDS = (
    "title",
    "description",
    "summary",
    "targetAudience",
    "learningOutcomes",
    "formatDetails",
    "prerequisites",
    "level",
    "category",
    "tags",
    "estimatedHours",
)


class StructureUploadMixin:
    if TYPE_CHECKING:
        client: LatornAPIClient
        course_id: str
        course_info: dict[str, Any]
        structure: list[dict[str, Any]]

        def _save_course_data(self) -> None: ...

        def _get_store_path(self) -> Path: ...

        def find_local_media_references(self, text: str) -> list[str]: ...

        def find_stale_media_refs(self, text: str, folder: Path) -> list[tuple[str, Path]]: ...

        def upload_media_refs(self, text: str, folder: Path, label: str) -> str: ...

    def upload_structure(self, *, dry_run: bool = False, sync_deletions: bool = False) -> None:
        """Синхронизирует описание курса, модули и уроки из локального structure.json в Latorn.

        Создаёт модули и уроки, которых нет на сайте.
        Переименовывает существующие, если название изменилось.
        sync_deletions=True — дополнительно предлагает удалить модули и уроки,
        которые есть на Latorn, но отсутствуют в локальном structure.json.
        """
        mode = "dry-run" if dry_run else "upload"
        print(f"\n=== upload_structure ({mode}) ===")
        self._sync_course_metadata(dry_run=dry_run)
        self._sync_sections(dry_run=dry_run, sync_deletions=sync_deletions)
        print("=== upload_structure done ===\n")

    # ------------------------------------------------------------------
    # Описание курса
    # ------------------------------------------------------------------

    def _sync_course_metadata(self, *, dry_run: bool) -> None:
        self._upload_summary_media(dry_run=dry_run)

        local = self.course_info
        remote = self.client.get_course(self.course_id)

        changed = {
            k: local[k]
            for k in _COURSE_DESCRIPTION_FIELDS
            if k in local and local.get(k) != remote.get(k)
        }

        if not changed:
            print("Course metadata: no changes")
            return

        print(f"Course metadata: updating {list(changed.keys())}")
        if not dry_run:
            self.client.update_course(self.course_id, changed)

    def _upload_summary_media(self, *, dry_run: bool) -> None:
        """Грузит картинки описания курса на Latorn — как медиа обычного шага.

        В `summary` можно писать `<img src="cover.png">`, положив файл в корень
        курса рядом с `structure.json`. Перед отправкой описания файл уезжает
        через `/api/uploads/lesson-media`, а в `summary` (и в самом
        `structure.json`) остаётся адрес платформы. Так картинку не приходится
        вставлять в веб-редакторе: он переписывает разметку описания целиком.
        """
        summary = self.course_info.get("summary")
        if not isinstance(summary, str) or not summary:
            return

        course_root = self._get_store_path().parent
        refs = self.find_local_media_references(summary)
        stale = [path.name for _, path in self.find_stale_media_refs(summary, course_root)]
        if not refs and not stale:
            return

        if dry_run:
            print(f"Course summary: will upload media {refs + stale}")
            return

        updated = self.upload_media_refs(summary, course_root, "  Описание курса:")
        if updated == summary:
            return

        self.course_info["summary"] = updated
        self._save_course_data()

    # ------------------------------------------------------------------
    # Модули (sections)
    # ------------------------------------------------------------------

    def _sync_sections(self, *, dry_run: bool, sync_deletions: bool = False) -> None:
        for local_sec in self.structure:
            section_id = local_sec.get("section_id") or ""
            title = local_sec["section_title"]
            position = local_sec["section_position"]

            if section_id:
                remote_title = local_sec.get("section_info", {}).get("title", "")
                if title != remote_title:
                    print(f"  Module {position}: rename '{remote_title}' → '{title}'")
                    if not dry_run:
                        self.client.update_section(section_id, {"title": title})
                        local_sec.setdefault("section_info", {})["title"] = title
                        self._save_course_data()
                else:
                    print(f"  Module {position}: '{title}' — up to date")
            else:
                print(f"  Module {position}: create '{title}'")
                if not dry_run:
                    created = self.client.create_section(self.course_id, title, position)
                    section_id = created.get("id", "")
                    if not section_id:
                        print("    ERROR: Latorn did not return section id")
                        continue
                    local_sec["section_id"] = section_id
                    local_sec["section_info"] = created
                    self._save_course_data()

            if section_id:
                self._sync_lessons(local_sec, section_id, dry_run=dry_run, sync_deletions=sync_deletions)
                self._ensure_lesson_positions(local_sec, section_id, dry_run=dry_run)

        self._ensure_section_positions(dry_run=dry_run)

        if sync_deletions:
            self._delete_orphaned_sections(dry_run=dry_run)

    def _ensure_section_positions(self, *, dry_run: bool) -> None:
        """Сверяет позиции модулей с порядком в structure.json и чинит расхождения.

        `_sync_sections` отправляет на Latorn только переименования. Если модуль
        переставили (изменилась позиция, а название — нет), ветка «up to date» не
        трогает позицию, и порядок модулей на сайте расходится с structure.json.
        Двухфазный алгоритм:
        1. Паркует неверно расположенные модули на временные позиции (>> max).
        2. Ставит каждый модуль на целевую позицию (от меньшей к большей).
        """
        managed = [s for s in self.structure if s.get("section_id")]
        target: dict[str, int] = {s["section_id"]: s["section_position"] for s in managed}
        if not target:
            return

        current_sections = self.client.list_sections(self.course_id)
        current_pos: dict[str, int] = {
            s["id"]: s["position"] for s in current_sections if s["id"] in target
        }

        misplaced: dict[str, tuple[int, int]] = {
            sid: (current_pos[sid], tgt)
            for sid, tgt in target.items()
            if sid in current_pos and current_pos[sid] != tgt
        }

        if not misplaced:
            print("  Section positions: all correct")
            return

        for sid, (cur, tgt) in misplaced.items():
            print(f"  Section position mismatch: section {sid}: Latorn={cur}, expected={tgt}")

        if dry_run:
            for sid, (_, tgt) in misplaced.items():
                print(f"  will fix section {sid} → {tgt}")
            return

        max_pos = max((s["position"] for s in current_sections), default=0)

        # Phase 1: паркуем неверные модули на временные позиции (>> max)
        temp_positions: dict[str, int] = {}
        for i, sid in enumerate(misplaced):
            temp_pos = max_pos + 100 + i
            try:
                self.client.update_section(sid, {"position": temp_pos})
                temp_positions[sid] = temp_pos
                print(f"    section {sid}: parked at {temp_pos}")
            except Exception as exc:
                print(f"    section {sid}: failed to park: {exc}")

        # Phase 2: ставим каждый модуль на целевую позицию (от меньшей к большей)
        for sid, (_, target_pos) in sorted(misplaced.items(), key=lambda kv: kv[1][1]):
            if sid not in temp_positions:
                continue
            try:
                self.client.update_section(sid, {"position": target_pos})
                print(f"    section {sid}: moved → {target_pos}")
            except Exception as exc:
                print(f"    section {sid}: failed to move to {target_pos}: {exc}")

        # Обновляем локальные section_info.position, чтобы structure.json отражал новый порядок
        for entry in managed:
            if entry["section_id"] in target:
                entry.setdefault("section_info", {})["position"] = entry["section_position"]
        self._save_course_data()

    def _delete_orphaned_sections(self, *, dry_run: bool) -> None:
        remote_sections = self.client.list_sections(self.course_id)
        local_section_ids: set[str] = {
            s["section_id"] for s in self.structure if s.get("section_id")
        }
        orphaned = [s for s in remote_sections if s["id"] not in local_section_ids]
        for section_info in orphaned:
            sid = section_info["id"]
            title = section_info.get("title", f"id={sid}")
            confirmed = _confirm(f"  Delete module '{title}' (id={sid}) from Latorn?")
            if confirmed:
                print(f"  Module '{title}': deleting")
                if not dry_run:
                    self.client.delete_section(sid)
            else:
                print(f"  Module '{title}': skipped")

    # ------------------------------------------------------------------
    # Уроки внутри модуля
    # ------------------------------------------------------------------

    def _sync_lessons(
        self, local_sec: dict[str, Any], section_id: str, *, dry_run: bool, sync_deletions: bool = False
    ) -> None:
        for lesson_entry in local_sec.get("lessons", []):
            lesson_id = lesson_entry.get("lesson_id") or ""
            title = lesson_entry["lesson_title"]
            position = lesson_entry["position"]

            if not lesson_id:
                # черновик — создаём урок
                print(f"    Lesson {position}: create '{title}'")
                if not dry_run:
                    created_lesson = self.client.create_lesson(section_id, title, position)
                    lesson_id = created_lesson.get("id", "")
                    if not lesson_id:
                        print("      ERROR: Latorn did not return lesson id")
                        continue
                    lesson_entry["lesson_id"] = lesson_id
                    lesson_entry["lesson_info"] = created_lesson
                    self._save_course_data()
                continue

            # урок существует — проверяем принадлежность модулю
            remote_section_id = lesson_entry.get("lesson_info", {}).get("sectionId", "")
            if remote_section_id and remote_section_id != section_id:
                print(f"    Lesson {position}: move '{title}' to this module")
                if not dry_run:
                    self.client.update_lesson(
                        lesson_id, {"sectionId": section_id, "position": position}
                    )
                    lesson_entry.setdefault("lesson_info", {})["sectionId"] = section_id
                    self._save_course_data()

            # проверяем название
            remote_title = lesson_entry.get("lesson_info", {}).get("title", "")
            if title != remote_title:
                print(f"    Lesson {position}: rename '{remote_title}' → '{title}'")
                if not dry_run:
                    self.client.update_lesson(lesson_id, {"title": title})
                    lesson_entry.setdefault("lesson_info", {})["title"] = title
                    self._save_course_data()
            else:
                print(f"    Lesson {position}: '{title}' — up to date")

        if sync_deletions:
            self._delete_orphaned_lessons(local_sec, section_id, dry_run=dry_run)

    def _ensure_lesson_positions(
        self, local_sec: dict[str, Any], section_id: str, *, dry_run: bool
    ) -> None:
        """Сверяет позиции уроков модуля с порядком в structure.json и чинит расхождения.

        `_sync_lessons` переименовывает уроки и создаёт недостающие, но никогда не
        переставляет уже существующие. Без этой сверки порядок уроков на сайте
        расходится с локальным structure.json — например, после вставки нового
        урока в середину модуля.

        Алгоритм двухфазный, как в `_ensure_step_positions`:
        1. Паркует неверно расположенные уроки на временные позиции (>> max).
        2. Ставит каждый урок на целевую позицию из structure.json.
        """
        managed = [e for e in local_sec.get("lessons", []) if e.get("lesson_id")]
        target: dict[str, int] = {e["lesson_id"]: e["position"] for e in managed}
        if not target:
            return

        current_lessons = self.client.list_lessons(section_id)
        current_pos: dict[str, int] = {
            l["id"]: l["position"] for l in current_lessons if l["id"] in target
        }

        misplaced: dict[str, tuple[int, int]] = {
            lid: (current_pos[lid], tgt)
            for lid, tgt in target.items()
            if lid in current_pos and current_pos[lid] != tgt
        }

        if not misplaced:
            print("    Lesson positions: all correct")
            return

        for lid, (cur, tgt) in misplaced.items():
            print(f"    Lesson position mismatch: lesson {lid}: Latorn={cur}, expected={tgt}")

        if dry_run:
            for lid, (_, tgt) in misplaced.items():
                print(f"    will fix lesson {lid} → {tgt}")
            return

        # Latorn держит уникальность (sectionId, position) в пределах модуля и
        # НЕ выполняет атомарный обмен позициями: PUT существующего урока на
        # занятую позицию отвечает 500 (пустое тело — необработанное исключение
        # уникальности), на позицию за пределами [1..N] — 400/500, а свободных
        # слотов внутри [1..N] нет. Поэтому «парковка» на временную позицию
        # невозможна, и перестановка уже созданных уроков через REST недоступна.
        # Пробуем выставить целевые позиции напрямую, затем сверяем фактический
        # результат: если API не справился — не оставляем мусора и печатаем
        # точную инструкцию на ручную перестановку в веб-редакторе.
        id_to_title = {e["lesson_id"]: e["lesson_title"] for e in managed}

        for lid, target_pos in sorted(target.items(), key=lambda kv: kv[1]):
            if current_pos.get(lid) == target_pos:
                continue
            try:
                self.client.update_lesson(lid, {"position": target_pos})
                print(f"      lesson {lid}: set → {target_pos}")
            except LatornAPIError as exc:
                print(f"      lesson {lid}: API отклонил позицию {target_pos} (HTTP {exc.status_code})")

        after_pos = {l["id"]: l["position"] for l in self.client.list_lessons(section_id)}
        still_wrong = {
            lid: (after_pos.get(lid), tgt)
            for lid, tgt in target.items()
            if after_pos.get(lid) != tgt
        }

        if not still_wrong:
            print("    Lesson positions: fixed")
            for entry in managed:
                entry.setdefault("lesson_info", {})["position"] = entry["position"]
            self._save_course_data()
            return

        print("    ! Не удалось переставить уроки через Latorn API — перестановка")
        print("      существующих уроков платформой не поддерживается. Переставьте")
        print("      вручную в веб-редакторе Latorn. Нужный порядок:")
        for lid, tgt in sorted(target.items(), key=lambda kv: kv[1]):
            print(f"        {tgt}. {id_to_title.get(lid, lid)} (id={lid}, сейчас на позиции {after_pos.get(lid)})")

    def _delete_orphaned_lessons(
        self, local_sec: dict[str, Any], section_id: str, *, dry_run: bool
    ) -> None:
        remote_lessons = self.client.list_lessons(section_id)
        local_lesson_ids: set[str] = {
            e["lesson_id"] for e in local_sec.get("lessons", []) if e.get("lesson_id")
        }
        for lesson_info in remote_lessons:
            lesson_id = lesson_info["id"]
            if lesson_id in local_lesson_ids:
                continue
            title = lesson_info.get("title", f"id={lesson_id}")
            confirmed = _confirm(f"    Delete lesson '{title}' (id={lesson_id})?")
            if confirmed:
                print(f"    Lesson '{title}': deleting")
                if not dry_run:
                    self.client.delete_lesson(lesson_id)
            else:
                print(f"    Lesson '{title}': skipped")
