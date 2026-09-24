"""Утилиты для работы с HTML-фрагментами и именами файлов.

Загрузчик регулярно преобразует HTML шагов в плоский текст для markdown,
извлекает названия шагов и нормализует имена файлов. Эти функции вынесены
в отдельный модуль, чтобы они не смешивались с API и файловой логикой.
"""

from __future__ import annotations

from html import unescape
import re


def sanitize_filename(name: str) -> str:
    """Удаляет из имени файла символы, запрещённые в Windows."""
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", name).strip()
    if len(sanitized) > 200:
        sanitized = sanitized[:200]
    return sanitized


def wrap_inline_code(value: str) -> str:
    """Оборачивает строку в backticks, подбирая безопасную длину ограды."""
    backtick_run = "`"
    while backtick_run in value:
        backtick_run += "`"
    return f"{backtick_run}{value}{backtick_run}"


def html_to_single_line_text(value: str) -> str:
    """Преобразует HTML в одну текстовую строку."""
    return " ".join(html_to_text(value).split())


def html_to_text(value: str) -> str:
    """Преобразует HTML шага в читаемый текст с базовым сохранением структуры."""
    if not value:
        return ""

    text = re.sub(
        r"<code[^>]*>(.*?)</code>",
        lambda match: wrap_inline_code(html_to_single_line_text(match.group(1))),
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|h[1-6]|li)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_step_name(html: str, max_length: int = 100) -> str:
    """Извлекает читаемое короткое название шага из HTML."""
    title = ""
    header_patterns = [
        r"<h1[^>]*>(.*?)</h1>",
        r"<h2[^>]*>(.*?)</h2>",
        r"<h3[^>]*>(.*?)</h3>",
        r"<h4[^>]*>(.*?)</h4>",
        r"<h5[^>]*>(.*?)</h5>",
        r"<h6[^>]*>(.*?)</h6>",
    ]

    for pattern in header_patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            title = match.group(1)
            break

    if not title:
        match = re.search(r"^#{1,6}\s+(.+)$", html, re.MULTILINE)
        if match:
            title = match.group(1)

    if not title:
        for pattern in (r"<strong[^>]*>(.*?)</strong>", r"<b[^>]*>(.*?)</b>"):
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                title = match.group(1)
                break

    if not title:
        text = " ".join(re.sub(r"<[^>]+>", "", html).split())
        if text:
            title = text.split(".")[0].strip()
        else:
            title = "Untitled"

    title = re.sub(r"<[^>]+>", "", title)
    title = " ".join(title.split())
    title = re.sub(
        r"^(Шаг|Step|Lesson|Урок)\s*\d+\s*[:\-\.\)]\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )

    if len(title) > max_length:
        title = title[:max_length].rsplit(" ", 1)[0]

    if not title or title.isspace():
        title = "Untitled"

    return title.strip()
