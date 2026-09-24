# latorn_downloader: заметки для разработчиков

Пакет отвечает за синхронизацию материалов Latorn (https://latorn.ru) с
локальной структурой курса. Основная точка входа для кода снаружи пакета:
`CourseDownloader` из `src.latorn_downloader`.

`CourseDownloader` является фасадом: он собирает в один класс несколько
смысловых mixin-модулей. Каждый mixin отвечает за отдельную область: структуру
курса, скачивание, загрузку или работу с локальными файлами. Такая организация
помогает добавлять новые типы шагов и новые операции в конкретном модуле, а не
разбирать один большой orchestration-файл.

## Модель данных Latorn

Курс → Модуль (section) → Урок (lesson) → Шаг (step). В отличие от Stepik,
у Latorn нет units — уроки принадлежат модулю напрямую. Все идентификаторы —
строки (cuid), курс дополнительно доступен по slug. Содержимое шага лежит в
поле `block`, тип шага — отдельное поле `type` в верхнем регистре
(`TEXT`, `VIDEO`, `CODE`, `CHOICE`, `NUMBER`, `FREE_TEXT`, `SORTING`,
`MATCHING`, `REVIEW`).

Авторизация — сессионная cookie NextAuth (`LATORN_SESSION` или
`LATORN_EMAIL`+`LATORN_PASSWORD` в `.env`); API-ключей у платформы нет.
Справочник API: `docs/latorn_api/`.

## Карта модулей

- `course_downloader.py` - фасад `CourseDownloader`, общая инициализация и
  хранилище структуры курса (`structure.json`).
- `api.py` - низкоуровневый HTTP-клиент Latorn API: авторизация, чтение
  сущностей, пагинация (`meta.hasNext`), CRUD модулей/уроков/шагов,
  загрузка файлов (`/api/uploads/lesson-media`).
- `course_structure.py` - загрузка метаданных курса, модулей, уроков и шагов.
- `download_workflow.py` - сценарий скачивания: обход модулей и уроков,
  сохранение шагов в локальные файлы.
- `upload_workflow.py` - сценарий загрузки локальных материалов на Latorn:
  построение локального плана урока, создание/перезапись шагов, удаление
  лишних поддерживаемых шагов.
- `structure_upload_workflow.py` - синхронизация описания курса, модулей и
  уроков из `structure.json` на Latorn.
- `lesson_manifest.py` - единственный источник истины о шагах урока:
  чтение/запись `lesson.json`, вычисление пути подпапки шага (`step_folder`),
  `load_manifest` / `save_manifest` / `upsert_step` / `get_step_entry`.
- `step_storage.py` - сериализация шагов: Latorn API ↔ локальные файлы.
  `save_step()` сохраняет шаг из API в подпапку и возвращает запись для
  `lesson.json`; `load_step()` читает локальные файлы и собирает `block` для
  загрузки обратно на платформу. Для задач (`code`) код `preCode` и сьютов
  выносится в отдельные `.py` (`precode.py`, `tests.py` / `tests/NN.py`) и
  подставляется обратно при загрузке; фолбэк — инлайн-значения из `step.json`.
- `local_steps.py` - работа с локальными HTML-файлами шагов, медиа (картинки и
  аудио — `download_step_media`) и относительными ссылками. Имя сохраняемого
  файла: атрибут `name` тега → имя из `src` без служебного хвоста Latorn
  (`01-<ts>-<uuid>.png` → `01.png`) → сверка по содержимому с файлами в папке
  шага, чтобы не плодить дубликаты; повторы получают суффиксы `_2`, `_3`.
- `step_types.py` - константы поддерживаемых типов шагов и маппинг
  локальных имён (`text`, `free-text`, ...) на типы API (`TEXT`, `FREE_TEXT`, ...).
- `html_utils.py` - небольшие функции для очистки HTML и получения текста.
- `html_format.py` - `format_step_html()`: раскладывает по строкам `task.html`,
  пришедший одной строкой из редактора Latorn. Уже размеченный текст
  возвращает без изменений. Её же вызывает хук `.claude/hooks/format_html.py`.
- `cli.py` и `__main__.py` - минимальные точки запуска из командной строки.

## Локальный формат шага

Каждый шаг — подпапка `{pos:02d}_{type_safe}/` (`type_safe` = локальный тип
с `-`→`_`). Содержимое:

| Тип | Файлы |
| --- | --- |
| `text` | `task.md` \| `task.html` (+ `step.json`, если в `block` есть поля кроме `text`) |
| `code` | `task.md` \| `task.html` + `step.json` (block без `text`, `preCode`, кода сьютов) + `precode.py` (`block.preCode`) + `tests.py` \| `tests/NN.py` (код сьютов) |
| `review` | `task.md` \| `task.html` + `step.json` (block без поля `text`) |
| `video` | `step.json` (block целиком) |
| `choice`, `number`, `free-text`, `sorting`, `matching` | `step.json` (block целиком) |

Имя файла с текстом зависит от `block.format`: `"markdown"` → `task.md`,
`"html"` (или отсутствие поля) → `task.html`. Для нового локального шага
дефолт — markdown: достаточно создать `task.md`, uploader сам подставит
`"format": "markdown"` в `block`.

Схема `block` у Latorn своя для каждого типа, и сервер строго проверяет её
при публикации. Хранение построено без потерь: всё, что пришло в `block`,
сохраняется и при загрузке собирается обратно один в один. Поэтому отдельных
обработчиков на каждый тип тестовой задачи (как было для Stepik) не требуется.

## Основные потоки данных

### Скачивание

1. `CourseDownloader.download_course()` из `download_workflow.py` выбирает
   нужные модуль, урок и шаг.
2. `get_lesson_steps()` из `course_structure.py` получает шаги через
   `GET /api/steps?lessonId=...` (редакторский список, нужны права
   преподавателя) и оставляет только типы из `SUPPORTED_STEP_TYPES`.
3. Каждый шаг сохраняется через `step_storage.save_step()`, которая создаёт
   подпапку `{pos:02d}_{type}/` и записывает нужные файлы.
4. Метаданные шага (`step_id`, `type`) добавляются в `lesson.json` через
   `lesson_manifest.upsert_step()` / `lesson_manifest.save_manifest()`.

### Загрузка

1. `CourseDownloader.upload_course()` из `upload_workflow.py` читает `lesson.json`
   через `lesson_manifest.load_manifest()`.
2. Для каждого шага из манифеста `step_storage.load_step()` читает локальные
   файлы и собирает Latorn `block`.
3. `_write_lesson_steps_by_position()` создаёт или перезаписывает шаги на
   платформе (`POST /api/steps`, `PUT /api/steps/[id]`).
4. При загрузке урока целиком `_ensure_step_positions()` чинит порядок шагов,
   а `_delete_extra_lesson_steps()` удаляет лишние поддерживаемые шаги вне
   локального плана.

## Публичные точки

- Основной импорт: `from src.latorn_downloader import CourseDownloader`.
- Основные операции: `download_course()`, `upload_course()`,
  `upload_structure()`, `refresh_structure()`.
- Вспомогательные методы, которые могут использовать скрипты вокруг пакета:
  `sanitize_filename()`, `extract_step_name()`, `get_theory()`, `delete_store()`.
- Для работы с манифестом урока вне пакета используйте функции из
  `lesson_manifest`: `load_manifest()`, `save_manifest()`, `upsert_step()`,
  `get_step_entry()`, `step_folder()`.
- Для прямой сериализации шагов используйте `step_storage.save_step()` и
  `step_storage.load_step()`.

## Проверки после изменений

Быстрая проверка синтаксиса:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src\latorn_downloader
```

Проверка базовых импортов:

```powershell
.\.venv\Scripts\python.exe -c "from src.latorn_downloader import CourseDownloader; print(CourseDownloader.__name__)"
```

Для изменений в загрузке на платформу сначала используйте `dry_run=True`, чтобы
увидеть план без записи в Latorn.
