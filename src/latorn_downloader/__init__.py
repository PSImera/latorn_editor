"""Пакет для загрузки структуры и материалов курса из Latorn.

Модуль экспортирует основной orchestration-класс `CourseDownloader`
и API-клиент `LatornAPIClient`. Остальные подмодули разделяют
ответственность между сетевой логикой, форматированием HTML и хранением
файлов шагов. Карта модулей и правила расширения описаны в `README.md`
рядом с кодом.
"""

from .api import LatornAPIClient
from .course_downloader import CourseDownloader

__all__ = ["CourseDownloader", "LatornAPIClient"]
