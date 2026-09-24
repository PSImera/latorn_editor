"""Сценарий загрузки локальных материалов обратно в Latorn."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .lesson_manifest import load_manifest, save_manifest, upsert_step, step_folder
from .step_storage import load_step
from .step_types import API_TYPE_BY_LOCAL, LOCAL_TYPE_BY_API, MAX_LESSON_STEPS, SUPPORTED_STEP_TYPES

if TYPE_CHECKING:
    from .api import LatornAPIClient


class UploadWorkflowMixin:
    if TYPE_CHECKING:
        client: LatornAPIClient
        structure: list[dict[str, Any]]
        root_folder: Path
        SUPPORTED_STEP_TYPES: set[str]

        def upload_structure(self, *, dry_run: bool = False, sync_deletions: bool = False) -> None: ...  # noqa: ARG002

        # LocalStepsMixin
        def upload_step_media(self, block: dict[str, Any], folder: Path, step_num: int) -> bool: ...  # noqa: ARG002

        def _print_local_asset_warning(self, step_num: int, step_html: str) -> None: ...  # noqa: ARG002

    def upload_course(
        self,
        module_num: int | None = None,
        lesson_num: int | None = None,
        step_num: int | None = None,
        *,
        dry_run: bool = False,
        steps_per_lesson: int = MAX_LESSON_STEPS,
        sync_structure: bool = False,
    ) -> None:
        """Загружает локальные материалы обратно в существующий курс Latorn.

        sync_structure=True — сначала синхронизирует описание курса, модули и уроки
        из structure.json в Latorn (создаёт/переименовывает), затем загружает шаги.
        """
        if sync_structure:
            self.upload_structure(dry_run=dry_run, sync_deletions=True)
        if module_num is not None:
            sections_to_process = [(module_num, self.structure[module_num - 1])]
        else:
            sections_to_process = list(enumerate(self.structure, 1))

        stats = {
            "uploaded": 0,
            "planned": 0,
            "missing": 0,
            "created": 0,
            "deleted": 0,
            "invalid": 0,
            "failed": 0,
        }

        for section_num, section in sections_to_process:
            section_title = section["section_title"]
            lessons_list = section["lessons"]

            if lesson_num is not None:
                lessons_to_process = [(lesson_num, lessons_list[lesson_num - 1])]
            else:
                lessons_to_process = list(enumerate(lessons_list, 1))

            print(f"\nModule {section_num}: {section_title}")

            for current_lesson_num, lesson_info in lessons_to_process:
                lesson_id = lesson_info["lesson_id"]
                lesson_title = lesson_info["lesson_title"]
                lesson_folder = (
                    self.root_folder / f"{section_num:02d}" / f"{current_lesson_num:02d}"
                )

                print(f"  Lesson {current_lesson_num}: {lesson_title}")

                if not lesson_id:
                    print("    SKIP: lesson is a draft — run upload_structure() first")
                    stats["missing"] += 1
                    continue

                lesson_folder.mkdir(parents=True, exist_ok=True)
                manifest = load_manifest(lesson_folder)
                manifest.setdefault("lesson_id", lesson_id)
                steps_in_manifest = manifest.get("steps", [])

                if not steps_in_manifest:
                    if step_num is not None:
                        print("    No steps in lesson.json")
                        stats["missing"] += 1
                        continue
                    # Полная синхронизация: оба пустые → заглушка; удалённый непустой → ошибка
                    all_lesson_steps = self.client.list_steps(lesson_id)
                    if all_lesson_steps:
                        print(
                            f"    ERROR: remote has {len(all_lesson_steps)} step(s) "
                            "but local is empty — skipping to avoid overwriting with nothing"
                        )
                        stats["failed"] += 1
                        continue
                    # Оба пустые — создаём заглушку
                    if dry_run:
                        print("    will create stub (both local and remote are empty)")
                        stats["planned"] += 1
                        continue
                    step_dir = lesson_folder / "01_text"
                    step_dir.mkdir(exist_ok=True)
                    (step_dir / "task.md").write_text(
                        "Урок в разработке.", encoding="utf-8"
                    )
                    upsert_step(manifest, {"position": 1, "step_id": None, "type": "text"})
                    save_manifest(lesson_folder, manifest)
                    steps_in_manifest = manifest["steps"]
                    print("    stub created")

                if step_num is not None:
                    steps_in_manifest = [
                        s for s in steps_in_manifest if s["position"] == step_num
                    ]

                all_lesson_steps = self.client.list_steps(lesson_id)

                upload_results = self._write_lesson_steps_by_position(
                    lesson_id=lesson_id,
                    all_lesson_steps=all_lesson_steps,
                    manifest_steps=steps_in_manifest,
                    manifest=manifest,
                    lesson_folder=lesson_folder,
                    steps_per_lesson=steps_per_lesson,
                    dry_run=dry_run,
                )
                for result in upload_results:
                    stats[result] += 1

                if step_num is None:
                    self._ensure_step_positions(
                        lesson_id=lesson_id,
                        manifest_steps=steps_in_manifest,
                        dry_run=dry_run,
                    )

                    local_positions = {s["position"] for s in steps_in_manifest}
                    local_step_ids = {
                        s["step_id"]
                        for s in steps_in_manifest
                        if s.get("step_id")
                    }
                    cleanup_results = self._delete_extra_lesson_steps(
                        lesson_id=lesson_id,
                        local_step_numbers=local_positions,
                        local_step_ids=local_step_ids,
                        steps_per_lesson=steps_per_lesson,
                        dry_run=dry_run,
                    )
                    for result in cleanup_results:
                        stats[result] += 1

        mode = "dry-run" if dry_run else "upload"
        print(
            f"\nDone ({mode}): "
            f"updated {stats['uploaded']}, "
            f"created {stats['created']}, "
            f"deleted {stats['deleted']}, "
            f"errors {stats['failed']}, "
            f"planned {stats['planned']}, "
            f"missing {stats['missing']}, "
            f"invalid {stats['invalid']}"
        )

    def _write_lesson_steps_by_position(
        self,
        lesson_id: str,
        all_lesson_steps: list[dict[str, Any]],
        manifest_steps: list[dict[str, Any]],
        manifest: dict[str, Any],
        lesson_folder: Path,
        *,
        steps_per_lesson: int,
        dry_run: bool,
    ) -> list[str]:
        results: list[str] = []
        remote_by_id: dict[str, dict[str, Any]] = {s["id"]: s for s in all_lesson_steps}
        remote_by_position: dict[int, dict[str, Any]] = {
            s["position"]: s for s in all_lesson_steps
        }
        # ID шагов, уже привязанных к конкретным записям манифеста — не отдавать при поиске
        # по позиции. Берём из ПОЛНОГО манифеста, а не из manifest_steps: при загрузке одного
        # шага (step_num) фильтр оставил бы один элемент, и чужой id не был бы защищён —
        # шаг с пустым id подобрался бы по позиции к чужому удалённому шагу и затёр его.
        claimed_ids: set[str] = {
            e["step_id"]
            for e in manifest.get("steps", manifest_steps)
            if e.get("step_id")
        }

        for manifest_entry in sorted(manifest_steps, key=lambda s: s["position"]):
            position = manifest_entry["position"]
            step_type = manifest_entry.get("type", "?")
            api_type = API_TYPE_BY_LOCAL.get(step_type)
            if position > steps_per_lesson:
                # Лимит редакторский, а не платформенный: Latorn держит и больше шагов.
                # Предупреждаем, но выгружаем — иначе локальный шаг остаётся без пары
                # на сайте, а очистка потом принимает его за лишний.
                print(f"    Step {position}: WARNING: over recommended limit {steps_per_lesson}")
            if api_type is None:
                print(f"    Step {position}: unknown type {step_type!r}")
                results.append("invalid")
                continue

            try:
                block, label = load_step(
                    manifest_entry=manifest_entry,
                    lesson_folder=lesson_folder,
                )
            except Exception as exc:
                print(f"    Step {position}: parse error: {exc}")
                results.append("invalid")
                continue

            # Ищем шаг: сначала по step_id, потом по позиции (только незанятые)
            manifest_step_id = manifest_entry.get("step_id")
            existing: dict[str, Any] | None = None
            if manifest_step_id:
                existing = remote_by_id.get(manifest_step_id)
            if existing is None:
                pos_step = remote_by_position.get(position)
                if pos_step and pos_step["id"] not in claimed_ids:
                    existing = pos_step

            if dry_run:
                action = "will update" if existing else "will create"
                print(f"    Step {position}: {action}: [{step_type}] {label}")
                self._print_local_asset_warning(position, str(block.get("text", "")))
                results.append("planned")
                continue

            # Картинки шага грузим сами: иначе их пришлось бы вставлять в
            # веб-редакторе, а он переписывает разметку всего шага.
            self.upload_step_media(block, step_folder(lesson_folder, position, step_type), position)

            try:
                if existing:
                    step_id = existing["id"]
                    patch: dict[str, Any] = {"block": block, "position": position}
                    # Тип передаём только при смене: PUT со сменой типа требует
                    # совместимый block, который мы и так отправляем целиком.
                    if existing.get("type") != api_type:
                        patch["type"] = api_type
                    self.client.update_step(step_id, patch)
                    if manifest_step_id != step_id:
                        upsert_step(manifest, {**manifest_entry, "step_id": step_id})
                        save_manifest(lesson_folder, manifest)
                        print(f"      step_id saved: {step_id}")
                    print(f"    Step {position}: updated: [{step_type}] {label}")
                    results.append("uploaded")
                else:
                    created = self.client.create_step(
                        lesson_id=lesson_id,
                        step_type=api_type,
                        block=block,
                        position=position,
                    )
                    step_id = created.get("id")
                    if not step_id:
                        raise RuntimeError("Latorn did not return step id")
                    upsert_step(manifest, {**manifest_entry, "step_id": step_id})
                    save_manifest(lesson_folder, manifest)
                    print(f"    Step {position}: created: [{step_type}] {label}")
                    results.append("created")
            except Exception as exc:
                print(f"    Step {position}: error: {label}")
                print(f"      {exc}")
                results.append("failed")

        return results

    def _ensure_step_positions(
        self,
        lesson_id: str,
        manifest_steps: list[dict[str, Any]],
        *,
        dry_run: bool,
    ) -> None:
        """Проверяет позиции шагов на Latorn и исправляет несоответствия.

        Алгоритм двухфазный, чтобы избежать конфликтов позиций:
        1. Паркует неверно расположенные шаги на временные позиции (>> max).
        2. Ставит каждый шаг на целевую позицию из lesson.json.
        """
        current_steps = self.client.list_steps(lesson_id)
        by_id: dict[str, dict[str, Any]] = {s["id"]: s for s in current_steps}

        target: dict[str, int] = {
            entry["step_id"]: entry["position"]
            for entry in manifest_steps
            if entry.get("step_id") and entry["step_id"] in by_id
        }

        misplaced: dict[str, tuple[int, int]] = {
            step_id: (by_id[step_id]["position"], target_pos)
            for step_id, target_pos in target.items()
            if by_id[step_id]["position"] != target_pos
        }

        if not misplaced:
            print("    Positions: all correct")
            return

        for step_id, (cur, tgt) in misplaced.items():
            print(f"    Position mismatch: step {step_id}: Latorn={cur}, expected={tgt}")

        if dry_run:
            for step_id, (_, tgt) in misplaced.items():
                print(f"    will fix step {step_id} → {tgt}")
            return

        max_pos = max((s["position"] for s in current_steps), default=0)

        # Phase 1: переносим неверные шаги на временные позиции (>> max)
        temp_positions: dict[str, int] = {}
        for i, step_id in enumerate(misplaced):
            temp_pos = max_pos + 100 + i
            try:
                self.client.update_step(step_id, {"position": temp_pos})
                temp_positions[step_id] = temp_pos
                print(f"      step {step_id}: parked at {temp_pos}")
            except Exception as exc:
                print(f"      step {step_id}: failed to park: {exc}")

        # Phase 2: ставим каждый шаг на целевую позицию (от меньшей к большей)
        for step_id, (_, target_pos) in sorted(misplaced.items(), key=lambda kv: kv[1][1]):
            if step_id not in temp_positions:
                continue
            try:
                self.client.update_step(step_id, {"position": target_pos})
                print(f"      step {step_id}: moved → {target_pos}")
            except Exception as exc:
                print(f"      step {step_id}: failed to move to {target_pos}: {exc}")

    def _delete_extra_lesson_steps(
        self,
        lesson_id: str,
        local_step_numbers: set[int],
        steps_per_lesson: int,
        *,
        local_step_ids: set[str] | None = None,
        dry_run: bool,
    ) -> list[str]:
        all_lesson_steps = self.client.list_steps(lesson_id)
        to_delete = [
            s for s in all_lesson_steps
            if self._should_delete_extra_step(s, local_step_numbers, steps_per_lesson, local_step_ids)
        ]
        results: list[str] = []
        for step in sorted(to_delete, key=lambda s: s.get("position", 0), reverse=True):
            result = self._delete_latorn_step(step=step, dry_run=dry_run)
            results.append(result)
        return results

    def _should_delete_extra_step(
        self,
        step: dict[str, Any],
        local_step_numbers: set[int],
        steps_per_lesson: int,  # noqa: ARG002 — оставлен для совместимости вызова
        local_step_ids: set[str] | None = None,
    ) -> bool:
        """Шаг на Latorn считается лишним, только если его нет в lesson.json.

        Превышение `steps_per_lesson` удалением не карается: лимит редакторский,
        и раньше эта ветка сносила на сайте шаги, которые локально есть, — вместе
        с их содержимым.
        """
        position = step.get("position", 0)
        step_type = LOCAL_TYPE_BY_API.get(step.get("type", ""))
        if step_type not in SUPPORTED_STEP_TYPES:
            return False
        if local_step_ids is not None:
            return step["id"] not in local_step_ids
        return position not in local_step_numbers

    def _delete_latorn_step(self, step: dict[str, Any], *, dry_run: bool) -> str:
        position = step.get("position", 0)
        step_type = LOCAL_TYPE_BY_API.get(step.get("type", ""), step.get("type", "?"))
        if dry_run:
            print(f"    Step {position}: will delete [{step_type}]")
            return "planned"
        self.client.delete_step(step["id"])
        print(f"    Step {position}: deleted [{step_type}]")
        return "deleted"
