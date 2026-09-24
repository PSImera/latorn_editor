"""Константы поддерживаемых типов шагов Latorn.

API Latorn использует типы в верхнем регистре (TEXT, CHOICE, FREE_TEXT, ...),
локально папки шагов именуются в нижнем регистре с дефисами
({pos:02d}_{type.replace('-', '_')}/): text, choice, free-text и т.д.
"""

# Шаги с только HTML-контентом
TEXT_STEP_TYPES: set[str] = {"text"}

# Видео-шаги — метаданные в step.json
VIDEO_STEP_TYPES: set[str] = {"video"}

# Задачи на программирование
CODE_STEP_TYPES: set[str] = {"code"}

# Работы с ручной проверкой преподавателем
REVIEW_STEP_TYPES: set[str] = {"review"}

# Тестовые задачи — хранятся как step.json
TEST_TASK_STEP_TYPES: set[str] = {
    "choice",
    "number",
    "free-text",
    "sorting",
    "matching",
}

SUPPORTED_STEP_TYPES: set[str] = (
    TEXT_STEP_TYPES
    | VIDEO_STEP_TYPES
    | CODE_STEP_TYPES
    | REVIEW_STEP_TYPES
    | TEST_TASK_STEP_TYPES
)

COMPLEX_STEP_TYPES: set[str] = CODE_STEP_TYPES | REVIEW_STEP_TYPES
HTML_STEP_TYPES: set[str] = TEXT_STEP_TYPES | CODE_STEP_TYPES | REVIEW_STEP_TYPES

MAX_LESSON_STEPS = 16

# Локальный тип (имя папки) → тип API Latorn
API_TYPE_BY_LOCAL: dict[str, str] = {
    "text": "TEXT",
    "video": "VIDEO",
    "code": "CODE",
    "choice": "CHOICE",
    "number": "NUMBER",
    "free-text": "FREE_TEXT",
    "sorting": "SORTING",
    "matching": "MATCHING",
    "review": "REVIEW",
}

# Тип API Latorn → локальный тип (имя папки)
LOCAL_TYPE_BY_API: dict[str, str] = {v: k for k, v in API_TYPE_BY_LOCAL.items()}
