"""HTTP-клиент для Latorn API.

Модуль инкапсулирует авторизацию через сессионную cookie NextAuth,
базовые запросы к REST API и пагинацию. Сетевая логика держится отдельно
от файловой структуры и форматирования материалов курса.

Особенности API Latorn (см. docs/latorn_api/):
- Все маршруты начинаются с /api; базовый адрес задаётся LATORN_BASE_URL.
- Идентификаторы — строки (cuid), курс дополнительно доступен по slug.
- Ответы-списки обёрнуты: {"meta": {...}, "courses": [...]}; одиночный
  GET /api/courses/[id] тоже возвращает {"courses": [{...}]}.
- Ошибки приходят в едином виде {"error": {"code", "message", "details"}}.
- Авторизация — сессионная cookie NextAuth; отдельных API-ключей нет.
"""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import httpx

load_dotenv()

# Стандартные имена сессионной cookie NextAuth/Auth.js: если имя не задано
# явно через LATORN_SESSION_COOKIE, значение выставляется под каждым из них —
# сервер просто игнорирует незнакомые cookie.
_SESSION_COOKIE_NAMES = (
    "__Secure-next-auth.session-token",
    "next-auth.session-token",
    "__Secure-authjs.session-token",
    "authjs.session-token",
)


class LatornAPIError(RuntimeError):
    """Ошибка Latorn API с кодом и телом ответа для диагностики."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class LatornAPIClient:
    """Небольшой клиент поверх Latorn API с авторизацией по cookie NextAuth."""

    def __init__(self, client: httpx.Client, base_url: str):
        self.client = client
        self.base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Авторизация
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "LatornAPIClient":
        """Создаёт клиента из переменных окружения.

        Способы авторизации (первый доступный):
        - LATORN_SESSION — значение сессионной cookie NextAuth из браузера
          (имя cookie можно уточнить через LATORN_SESSION_COOKIE);
        - LATORN_EMAIL + LATORN_PASSWORD — вход через NextAuth
          credentials-провайдер (/api/auth/csrf + /api/auth/callback/credentials).

        Опционально: LATORN_BASE_URL (по умолчанию https://latorn.ru).
        """
        base_url = os.getenv("LATORN_BASE_URL", "https://latorn.ru").rstrip("/")
        http_client = httpx.Client(
            http2=False,
            follow_redirects=True,
            timeout=30.0,
            headers={
                "Accept": "application/json",
                "Origin": base_url,
                "Referer": f"{base_url}/",
            },
        )

        api = cls(client=http_client, base_url=base_url)
        domain = httpx.URL(base_url).host

        session_cookie = os.getenv("LATORN_SESSION", "").strip()
        email = os.getenv("LATORN_EMAIL", "").strip()
        password = os.getenv("LATORN_PASSWORD", "")

        if session_cookie:
            cookie_name = os.getenv("LATORN_SESSION_COOKIE", "").strip()
            names = (cookie_name,) if cookie_name else _SESSION_COOKIE_NAMES
            for name in names:
                http_client.cookies.set(name, session_cookie, domain=domain)
        elif email and password:
            api._login_with_credentials(email, password)
        else:
            raise ValueError(
                "Для Latorn нужна авторизация: укажи в .env либо LATORN_SESSION "
                "(cookie сессии из браузера), либо LATORN_EMAIL и LATORN_PASSWORD."
            )

        api._verify_session()
        return api

    def _login_with_credentials(self, email: str, password: str) -> None:
        """Выполняет вход через NextAuth credentials-провайдер."""
        csrf_response = self.client.get(f"{self.base_url}/api/auth/csrf")
        csrf_response.raise_for_status()
        csrf_token = csrf_response.json().get("csrfToken", "")
        if not csrf_token:
            raise LatornAPIError("Не удалось получить csrfToken у /api/auth/csrf")

        provider = os.getenv("LATORN_AUTH_PROVIDER", "credentials")
        response = self.client.post(
            f"{self.base_url}/api/auth/callback/{provider}",
            data={
                "csrfToken": csrf_token,
                "email": email,
                "password": password,
                "json": "true",
            },
        )
        if response.status_code >= 400:
            raise LatornAPIError(
                f"Вход через NextAuth не удался (HTTP {response.status_code}). "
                "Проверь LATORN_EMAIL/LATORN_PASSWORD или используй LATORN_SESSION.",
                status_code=response.status_code,
            )

    def _verify_session(self) -> None:
        """Проверяет, что сессия действительна, запросом профиля."""
        response = self.client.get(f"{self.base_url}/api/profile")
        if response.status_code in (401, 403):
            raise LatornAPIError(
                "Сессия Latorn недействительна: /api/profile вернул "
                f"{response.status_code}. Обнови LATORN_SESSION в .env.",
                status_code=response.status_code,
            )
        response.raise_for_status()

    # ------------------------------------------------------------------
    # Курсы
    # ------------------------------------------------------------------

    def get_course(self, course_id: str) -> dict[str, Any]:
        """Возвращает курс по идентификатору или slug."""
        data = self._get_json(f"/api/courses/{course_id}")
        return self._extract_one(data, "course")

    def update_course(self, course_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Обновляет настройки/описание курса."""
        data = self._send_json("PUT", f"/api/courses/{course_id}", fields)
        return self._extract_one(data, "course")

    def get_course_analytics(self, course_id: str) -> dict[str, Any]:
        """Возвращает учебную аналитику курса (доступ: чтение аналитики).

        Помимо метрик содержит sectionStats/lessonStats/stepStats с id,
        названиями и позициями всех сущностей — используется как запасной
        источник структуры, когда /api/sections не пускает команду курса
        к неопубликованному черновику.
        """
        data = self._get_json(f"/api/instructor/courses/{course_id}/analytics")
        analytics = data.get("analytics", data)
        return analytics if isinstance(analytics, dict) else {}

    # ------------------------------------------------------------------
    # Модули (sections)
    # ------------------------------------------------------------------

    def list_sections(self, course_id: str) -> list[dict[str, Any]]:
        """Возвращает все модули курса, отсортированные по position."""
        sections = self._collect_paginated("/api/sections", "sections", {"courseId": course_id})
        sections.sort(key=lambda s: s.get("position", 0))
        return sections

    def get_section(self, section_id: str) -> dict[str, Any]:
        data = self._get_json(f"/api/sections/{section_id}")
        return self._extract_one(data, "section")

    def create_section(self, course_id: str, title: str, position: int) -> dict[str, Any]:
        data = self._send_json(
            "POST", "/api/sections",
            {"courseId": course_id, "title": title, "position": position},
        )
        return self._extract_one(data, "section")

    def update_section(self, section_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        data = self._send_json("PUT", f"/api/sections/{section_id}", fields)
        return self._extract_one(data, "section")

    def delete_section(self, section_id: str) -> None:
        self._send_delete(f"/api/sections/{section_id}")

    # ------------------------------------------------------------------
    # Уроки (lessons)
    # ------------------------------------------------------------------

    def list_lessons(self, section_id: str) -> list[dict[str, Any]]:
        """Возвращает все уроки модуля, отсортированные по position."""
        lessons = self._collect_paginated("/api/lessons", "lessons", {"sectionId": section_id})
        lessons.sort(key=lambda l: l.get("position", 0))
        return lessons

    def get_lesson(self, lesson_id: str) -> dict[str, Any]:
        data = self._get_json(f"/api/lessons/{lesson_id}")
        return self._extract_one(data, "lesson")

    def create_lesson(self, section_id: str, title: str, position: int) -> dict[str, Any]:
        data = self._send_json(
            "POST", "/api/lessons",
            {"sectionId": section_id, "title": title, "position": position},
        )
        return self._extract_one(data, "lesson")

    def update_lesson(self, lesson_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        data = self._send_json("PUT", f"/api/lessons/{lesson_id}", fields)
        return self._extract_one(data, "lesson")

    def delete_lesson(self, lesson_id: str) -> None:
        self._send_delete(f"/api/lessons/{lesson_id}")

    # ------------------------------------------------------------------
    # Шаги (steps)
    # ------------------------------------------------------------------

    def list_steps(self, lesson_id: str) -> list[dict[str, Any]]:
        """Возвращает все шаги урока для редактора, отсортированные по position."""
        steps = self._collect_paginated("/api/steps", "steps", {"lessonId": lesson_id})
        steps.sort(key=lambda s: s.get("position", 0))
        return steps

    def get_step(self, step_id: str) -> dict[str, Any]:
        data = self._get_json(f"/api/steps/{step_id}")
        return self._extract_one(data, "step")

    def create_step(
        self,
        lesson_id: str,
        step_type: str,
        block: dict[str, Any],
        position: int | None = None,
    ) -> dict[str, Any]:
        """Создаёт шаг; step_type — тип API Latorn (TEXT, CHOICE, ...)."""
        payload: dict[str, Any] = {
            "lessonId": lesson_id,
            "type": step_type,
            "block": block,
        }
        if position is not None:
            payload["position"] = position
        data = self._send_json("POST", "/api/steps", payload)
        return self._extract_one(data, "step")

    def update_step(self, step_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Обновляет шаг. При смене type обязателен совместимый block."""
        data = self._send_json("PUT", f"/api/steps/{step_id}", patch)
        return self._extract_one(data, "step")

    def delete_step(self, step_id: str) -> None:
        self._send_delete(f"/api/steps/{step_id}")

    # ------------------------------------------------------------------
    # Файлы
    # ------------------------------------------------------------------

    def upload_lesson_media(self, file_path: Path) -> dict[str, Any]:
        """Загружает изображение/аудио/WebM для урока; возвращает ответ с url."""
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        with file_path.open("rb") as fh:
            response = self.client.post(
                f"{self.base_url}/api/uploads/lesson-media",
                files={"file": (file_path.name, fh, content_type)},
            )
        self._raise_for_status(response)
        return response.json()

    def upload_course_cover(self, file_path: Path) -> dict[str, Any]:
        """Загружает обложку курса; возвращает ответ с url."""
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        with file_path.open("rb") as fh:
            response = self.client.post(
                f"{self.base_url}/api/uploads/course-cover",
                files={"file": (file_path.name, fh, content_type)},
            )
        self._raise_for_status(response)
        return response.json()

    def download_binary(self, url: str, *, timeout: int = 10) -> bytes:
        """Скачивает бинарный ресурс; относительные пути (/uploads/...)
        разрешаются от базового адреса Latorn."""
        if url.startswith("//"):
            url = f"https:{url}"
        elif url.startswith("/"):
            url = f"{self.base_url}{url}"
        response = self.client.get(url, timeout=timeout)
        response.raise_for_status()
        return response.content

    # ------------------------------------------------------------------
    # Внутренние помощники
    # ------------------------------------------------------------------

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(f"{self.base_url}{path}", params=params)
        self._raise_for_status(response)
        return response.json()

    def _send_json(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.request(method, f"{self.base_url}{path}", json=payload)
        self._raise_for_status(response)
        if not response.content:
            return {}
        return response.json()

    def _send_delete(self, path: str) -> None:
        response = self.client.delete(f"{self.base_url}{path}")
        if response.status_code not in (200, 204):
            self._raise_for_status(response)

    @staticmethod
    def _extract_one(data: Any, key: str) -> dict[str, Any]:
        """Достаёт один ресурс из ответа Latorn.

        Поддерживает варианты: сам объект, {"course": {...}},
        {"courses": [{...}]} — API использует именованные обёртки.
        """
        if not isinstance(data, dict):
            return {}
        for candidate_key in (key, f"{key}s"):
            if candidate_key in data:
                value = data[candidate_key]
                if isinstance(value, list):
                    return value[0] if value else {}
                if isinstance(value, dict):
                    return value
        return data

    def _collect_paginated(
        self,
        path: str,
        key: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Собирает все страницы списка в один список по meta.hasNext."""
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            request_params = dict(params or {})
            request_params["page"] = page
            data = self._get_json(path, params=request_params)
            items.extend(data.get(key, []))
            meta = data.get("meta", {})
            if not meta.get("hasNext"):
                break
            page = int(meta.get("page", page)) + 1
        return items

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Поднимает LatornAPIError с кодом и сообщением из тела ответа."""
        if response.status_code < 400:
            return
        code: str | None = None
        message = ""
        details: Any = None
        try:
            error = response.json().get("error", {})
            code = error.get("code")
            message = error.get("message", "")
            details = error.get("details")
        except Exception:
            message = response.text.strip()[:2000]
        if details:
            message = f"{message} details={json.dumps(details, ensure_ascii=False)[:2000]}"
        raise LatornAPIError(
            f"HTTP {response.status_code} {response.request.method} "
            f"{response.request.url}: [{code or '?'}] {message}",
            status_code=response.status_code,
            code=code,
        )
