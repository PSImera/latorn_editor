# Latorn Editor

> **Внимание:** это личный рабочий инструмент, не релизный продукт. API может меняться без предупреждения, обратная совместимость не гарантируется.

Инструмент для скачивания, редактирования и загрузки учебных материалов курсов на [Latorn](https://latorn.ru). Работает совместно с Claude Code — AI-агент читает `CLAUDE.md` и набор скилов, чтобы создавать и редактировать шаги уроков в нужном формате.

Справочник API платформы — в `docs/latorn_api/`.

## Требования

- Python 3.10+
- Node.js (для рендеринга Mermaid-диаграмм)

## Установка

### 1. Клонировать репозиторий

```powershell
git clone --recurse-submodules <repo-url>
```

Флаг `--recurse-submodules` автоматически скачивает сторонние скилы (например, `humanizer-ru`).  
Если уже клонировали без флага — выполните отдельно:

```powershell
git submodule update --init
```

### 2. Создать виртуальное окружение

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install torch --index-url https://download.pytorch.org/whl/cu126
pip install -r requirements.txt
```

### 3. Установить Mermaid CLI (для схем)

```powershell
npx -p @mermaid-js/mermaid-cli mmdc -h
```

### 4. Создать файл `.env`

У Latorn нет API-ключей — авторизация идёт через сессию NextAuth. Укажите **один** из способов:

```
# Способ 1: логин и пароль (вход выполняется автоматически)
LATORN_EMAIL=you@example.com
LATORN_PASSWORD=your_password

# Способ 2: cookie сессии из браузера
LATORN_SESSION=your_session_cookie_value
```

`LATORN_SESSION` — значение cookie сессии: откройте [latorn.ru](https://latorn.ru), DevTools → Application → Cookies → `__Secure-next-auth.session-token` (или похожая с `session-token` в имени).

Опционально: `LATORN_BASE_URL` (по умолчанию `https://latorn.ru`), `LATORN_SESSION_COOKIE` (точное имя cookie, если отличается от стандартных).

### 5. Настроить конфигурацию курсов

Скопируйте пример и заполните:

```powershell
copy course_config.example.json course_config.json
```

Отредактируйте `course_config.json`:

```json
{
    "default_course": "my_course",
    "courses": {
        "my_course": "latorn-course-id-or-slug"
    },
    "download": ["my_course"],
    "upload": []
}
```

| Поле | Описание |
|---|---|
| `default_course` | Курс, с которым работает Claude Code по умолчанию |
| `courses` | Справочник всех курсов: `"имя": "id-или-slug"` на Latorn |
| `download` | Список курсов для скачивания (`downloader.py`) |
| `upload` | Список курсов для загрузки (`uploader.py`) |

## Использование

### Узнать id/slug курса (включая черновики)

```powershell
.venv\Scripts\python.exe scripts\list_my_courses.py
```

Выводит все курсы, доступные аккаунту из `.env`: id, slug, название и статус
(`published`/`DRAFT`). Черновиков нет в публичном каталоге — их id/slug можно
узнать только так или из адресной строки браузера. В `course_config.json`
подходит и id, и slug.

### Скачать курс

Отредактируйте `"download"` в `course_config.json`, затем:

```powershell
.venv\Scripts\python.exe downloader.py
```

Раскомментируйте `module_num`, `lesson_num`, `step_num` в `downloader.py`, чтобы точечно указать что скачивать: конкретный модуль, урок или отдельный шаг.

### Загрузить курс на Latorn

Отредактируйте `"upload"` в `course_config.json`, затем:

```powershell
.venv\Scripts\python.exe uploader.py
```

По умолчанию в скрипте включён `dry_run=True` — выводится план без записи на сервер. Уберите флаг для реальной загрузки. Раскомментируйте `module_num`, `lesson_num`, `step_num`, чтобы точечно указать что загружать. `upload_structure()` синхронизирует описание курса, модули и уроки из `structure.json`.

### Обновить RAG-индекс

```powershell
.venv\Scripts\python.exe update_rag.py
```

Курс берётся из `default_course` в `course_config.json`.

### CLI: скачивание

```powershell
# Скачать курс по умолчанию целиком
.venv\Scripts\python.exe -m src.latorn_downloader
```

### CLI: RAG-индекс

```powershell
# Построить / обновить индекс
.venv\Scripts\python.exe -m src.course_rag build --course <course>

# Обновить один шаг
.venv\Scripts\python.exe -m src.course_rag update --course <course> --file courses/<course>/03/05/01_text/task.html

# Поиск
.venv\Scripts\python.exe -m src.course_rag search --course <course> --query "контекстное окно" --k 5

# Поиск только по пройденному материалу (до урока 5 модуля 3)
.venv\Scripts\python.exe -m src.course_rag search-history --course <course> --query "промпт" --module-to 3 --lesson-to 5
```

### Сгенерировать summary-файлы

Summary можно создавать двумя способами:

**Скриптом** — автоматически по всему курсу на основе TF-IDF:

```powershell
.venv\Scripts\python.exe -m src.utils.generate_lesson_summaries --course <course>
```

**Агентом (Claude Code + скил `summary`)** — вручную после завершения урока. Агент читает содержимое шагов и составляет осмысленное summary с учётом контекста курса. Используется командой `/summary` во время работы над уроком.

Оба способа создают файлы `XX-YY_summary.md` в папке урока.

### Извлечь структуру курса

Компактная выжимка из `structure.json` (описание курса, названия модулей и уроков):

```powershell
.venv\Scripts\python.exe -m src.utils.extract_course_structure --input courses/<course>/structure.json
```

## Модель данных Latorn

Курс → Модуль (section) → Урок (lesson) → Шаг (step). Идентификаторы — строки (cuid), курс дополнительно доступен по slug. Типы шагов: `TEXT`, `VIDEO`, `CODE`, `CHOICE`, `NUMBER`, `FREE_TEXT`, `SORTING`, `MATCHING`, `REVIEW` (локально — в нижнем регистре: `text`, `free-text`, ...).

Текст шага (`block.text`) в курсах, созданных на Latorn, — Markdown (`block.format = "markdown"`, локально `task.md`); в курсах, импортированных платформой со Stepik, — HTML (`block.format = "html"`, локально `task.html`). Контент хранится байт-в-байт как на сервере; для новых локальных шагов дефолт — Markdown.

## Структура проекта

```
latorn_editor/
├── courses/              # Учебные материалы
│   └── <course>/
│       ├── CLAUDE.md         # Правила для конкретного курса (опционально)
│       ├── structure.json    # Структура курса и описание (создаёт downloader)
│       └── <module>/         # Нумерация с нулями: 01, 02, ...
│           └── <lesson>/
│               ├── lesson.json         # Манифест шагов (авторитетный список)
│               └── <pos>_<type>/       # Папка шага: 01_text, 03_choice, ...
│                   ├── task.md         # Контент (text, code, review); task.html у HTML-курсов
│                   └── step.json       # block шага (тестовые задачи, video, доп. поля)
├── docs/
│   ├── latorn_api/       # Справочник API платформы
│   └── links-catalog.md  # Каталог перекрёстных ссылок между курсами
├── scripts/
│   └── list_my_courses.py # id/slug всех курсов аккаунта (включая черновики)
├── src/
│   ├── config.py          # EditorConfig
│   ├── latorn_downloader/ # Скачивание и загрузка (см. README пакета)
│   ├── course_rag/        # Векторный RAG-индекс
│   └── utils/             # Вспомогательные скрипты
├── .claude/
│   └── skills/            # Скилы для Claude Code
├── course_config.json         # Конфигурация курсов (не в git)
├── course_config.example.json # Шаблон конфига
├── downloader.py
├── uploader.py
└── update_rag.py
```

## Скилы Claude Code

Скилы подключаются автоматически при работе с материалами курсов через Claude Code. Вызываются командой `/skill-name`.

| Скил | Назначение |
|---|---|
| `lesson` | Редактирование шагов урока: текстовые шаги, структура `lesson.json` |
| `test-tasks` | Генерация тестовых задач (choice, matching и др.) |
| `task-programming` | Создание задач на программирование с автопроверкой |
| `task-rewiew` | Создание заданий с ревью человеком |
| `code-examples` | Валидация и оформление кодовых примеров |
| `summary` | Сохранение и чтение саммари уроков и модулей |
| `rag` | Семантический поиск по индексу курса |
| `shemas` | Генерация схем и диаграмм (Mermaid / SVG) |
| `context-links` | Перекрёстные ссылки между курсами (каталог — `docs/links-catalog.md`) |
| `public-materials` | Правила для публичного контента |
| `humanizer-ru` | Очеловечивание AI-сгенерированного текста на русском — by [Vladimir-Human](https://github.com/Vladimir-Human/humanizer-ru) |

## Каталог перекрёстных ссылок (links-catalog.md)

`docs/links-catalog.md` — реестр курсов и учёт использованных упоминаний. Скил `context-links` читает его при добавлении ссылок между курсами.

**Target Catalog** — метаданные каждого курса: `kind`, `local_folder`, id курса, `title`, `status`, промо- и реферальные ссылки.

**Local Course Link Ledger** — сколько упоминаний уже использовано (максимум 2 на пару курсов):

```markdown
### Source: `my_course`

- `local_folder`: `courses/my_course`
- `status`: `in_progress`
- `outbound_link_steps`:
  - `my_course`: `SELF_FORBIDDEN`
  - `other_course`: `[]`              # ещё не использовано
  - `another_course`: `["02-03-05"]`  # использовано один раз (модуль-урок-шаг)
- `violations`: `none`
```

Индекс шага в ledger — формат `MM-LL-SS` (модуль-урок-шаг).

> ⚠️ Сам каталог пока содержит промо-ссылки эпохи Stepik (`stepik_id`, `promo_page` и реферальные ссылки) — их нужно заменить на актуальные ссылки Latorn, когда они появятся.

## Курсо-специфичные правила

Файл `courses/<course>/CLAUDE.md` (внутри папки курса) содержит правила, которые Claude Code применяет только для этого курса: тематику, требования к стилю, структуру уроков.

## Переменные конфигурации EditorConfig

Основные параметры в `src/config.py`:

| Параметр | По умолчанию | Описание |
|---|---|---|
| `course_id` | `""` | id (cuid) или slug курса на Latorn |
| `course_name` | `""` | Имя папки в `courses/` |
| `course_save_folder` | `courses` | Корневая папка материалов |
| `encoding` | `utf-8` | Кодировка HTML-файлов |
| `rag_embedding_model` | `intfloat/multilingual-e5-large` | Модель эмбеддингов |
| `rag_embedding_offline` | `True` | Использовать локально кэшированную модель |
