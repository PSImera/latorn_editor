"""Локальные HTML-шаги, изображения и ссылки на ресурсы."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .html_utils import extract_step_name, sanitize_filename
from .media_convert import ConversionError, convert_for_upload, needs_conversion
from .step_storage import save_task_text

# Что аплоудер грузит на Latorn сам. Всё остальное (локальные .html, .py,
# архивы) отправляется ссылкой как есть — платформа их не принимает.
# Часть форматов платформа не принимает (`.gif`, `.mp4`, а схемы курса мы
# держим растром) — такие файлы перед отправкой конвертируются, см.
# `media_convert.CONVERSIONS`.
MEDIA_SUFFIXES = frozenset(
    ".png .jpg .jpeg .gif .webp .svg .mp3 .wav .ogg .m4a .mp4 .webm".split()
)

# Хвост, который Latorn дописывает к имени файла при загрузке в
# /uploads/lesson-media: "-<epoch ms>-<uuid>". У загрузок постарше имени
# в URL нет вовсе — там только "<epoch ms>-<uuid>", и восстанавливать нечего.
_UPLOAD_SUFFIX_RE = re.compile(
    r"^(?P<stem>.+)-(?P<ts>\d{13})-[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"
    r"(?P<ext>\.[A-Za-z0-9]+)?$",
    re.IGNORECASE,
)


def _uploaded_url(response: Any) -> str:
    """Достаёт адрес файла из ответа `/api/uploads/lesson-media`.

    Оболочка ответа у Latorn плавает от эндпоинта к эндпоинту (`url`,
    `{"file": {...}}`, `{"data": {...}}`), поэтому ищем первую строку,
    похожую на путь к загруженному файлу, на любой глубине.
    """
    if isinstance(response, str):
        return response if response.startswith(("/uploads/", "http://", "https://")) else ""
    if isinstance(response, dict):
        for key in ("url", "path", "src", "location"):
            found = _uploaded_url(response.get(key))
            if found:
                return found
        for value in response.values():
            found = _uploaded_url(value)
            if found:
                return found
    if isinstance(response, list):
        for value in response:
            found = _uploaded_url(value)
            if found:
                return found
    return ""


def _replace_asset_ref(text: str, ref: str, url: str) -> str:
    """Меняет ссылку на локальный файл на адрес платформы.

    Только в местах, где путь и правда путь: значения `src`/`href` и адрес
    markdown-ссылки. Совпадение с именем файла в прозе («картинка 01.png»)
    не трогаем.
    """
    quoted = re.escape(ref)
    text = re.sub(
        rf'(\b(?:src|href)=)(["\'])\s*{quoted}\s*\2',
        lambda m: f"{m.group(1)}{m.group(2)}{url}{m.group(2)}",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(rf"(!?\[[^\]]*\]\(\s*){quoted}(\s*[)\s])", rf"\g<1>{url}\g<2>", text)


def _strip_upload_suffix(filename: str) -> str:
    """`01-1785082668695-62cebddb-...-167ed2da35b7.png` → `01.png`."""
    m = _UPLOAD_SUFFIX_RE.match(filename)
    if m is None:
        return filename
    return m.group("stem") + (m.group("ext") or "")


if TYPE_CHECKING:
    from ..config import EditorConfig
    from .api import LatornAPIClient


class LocalStepsMixin:
    if TYPE_CHECKING:
        client: LatornAPIClient
        config: EditorConfig

    def read_step_html(self, filepath: Path | str) -> str:
        """Читает локальный HTML-файл шага как UTF-8/encoding из конфига."""
        html = Path(filepath).read_text(encoding=self.config.encoding).strip()
        body_match = re.search(
            r"<body[^>]*>(.*?)</body>",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if body_match is not None:
            return body_match.group(1).strip()

        return html

    def find_local_asset_references(self, text: str) -> list[str]:
        """Находит относительные ссылки на файлы, которых нет на Latorn.

        Понимает HTML (src/href) и markdown (`[текст](путь)`, `![alt](путь)`).
        Пути /uploads/... — это файлы, уже загруженные на Latorn,
        они не считаются локальными.
        """
        candidates = [
            m.group(1)
            for m in re.finditer(
                r"\b(?:src|href)=['\"]([^'\"]+)['\"]",
                text,
                flags=re.IGNORECASE,
            )
        ]
        candidates += [
            m.group(1) for m in re.finditer(r"!?\[[^\]]*\]\(\s*([^)\s]+)", text)
        ]

        references: list[str] = []
        for value in candidates:
            value = value.strip()
            if not value:
                continue
            if value.startswith(("#", "//", "http://", "https://")):
                continue
            if value.startswith(("data:", "mailto:", "tel:")):
                continue
            if value.startswith("/uploads/"):
                continue
            references.append(value)
        return references

    def find_local_media_references(self, text: str) -> list[str]:
        """Локальные ссылки на файлы, которые Latorn принимает как медиа."""
        return [
            ref
            for ref in dict.fromkeys(self.find_local_asset_references(text))
            if Path(ref).suffix.lower() in MEDIA_SUFFIXES
        ]

    def find_stale_media_refs(self, text: str, folder: Path) -> list[tuple[str, Path]]:
        """Уже загруженные медиа, у которых локальный файл новее платформенного.

        Отредактировали схему после выгрузки — в тексте стоит адрес платформы,
        локальной ссылки больше нет, и `upload_media_refs` такой файл не тронет:
        на Latorn останется старая картинка. Единственная зацепка — метка
        времени загрузки в самом адресе (`01-<epoch ms>-<uuid>.png`): если файл
        в папке шага изменён позже, значит платформенная копия устарела.

        Возвращает пары (адрес в тексте, локальный файл). Пропускает адреса без
        имени файла (загрузки постарше) и те, у которых в папке ничего нет.
        """
        urls = [
            m.group(1)
            for m in re.finditer(
                r"\b(?:src|href)=['\"](/uploads/[^'\"]+)['\"]",
                text,
                flags=re.IGNORECASE,
            )
        ]
        urls += [m.group(1) for m in re.finditer(r"!?\[[^\]]*\]\(\s*(/uploads/[^)\s]+)", text)]

        stale: list[tuple[str, Path]] = []
        for url in dict.fromkeys(urls):
            m = _UPLOAD_SUFFIX_RE.match(url.rsplit("/", 1)[-1])
            if m is None:
                continue
            name = m.group("stem") + (m.group("ext") or "")
            if Path(name).suffix.lower() not in MEDIA_SUFFIXES:
                continue
            path = folder / name
            if not path.is_file():
                continue
            if path.stat().st_mtime * 1000 <= int(m.group("ts")):
                continue
            stale.append((url, path))
        return stale

    def _upload_media_file(self, path: Path, label: str) -> str:
        """Грузит один файл на Latorn и возвращает его адрес ("" при неудаче).

        Форматы, которые платформа не принимает, конвертируются по дороге.
        """
        if needs_conversion(path):
            try:
                converted = convert_for_upload(path)
            except ConversionError as exc:
                print(f"{label} не удалось конвертировать {path.name}: {exc}")
                return ""
            if converted != path:
                print(f"{label} {path.name} → {converted.name} (Latorn не принимает {path.suffix})")
                path = converted
        try:
            url = _uploaded_url(self.client.upload_lesson_media(path))
        except Exception as exc:
            print(f"{label} не удалось загрузить {path.name}: {exc}")
            return ""
        if not url:
            print(f"{label} ответ на загрузку {path.name} без адреса файла")
        return url

    def upload_media_refs(self, text: str, folder: Path, label: str) -> str:
        """Грузит медиафайлы из текста на Latorn и подставляет их адреса.

        Общая часть для шагов и для описания курса: и там, и там текст ссылается
        на файлы по имени (`src="01.png"`, `src="cover.png"`), а платформе нужен
        адрес вида `/uploads/lesson-media/...`. `folder` — папка, относительно
        которой ищутся файлы: для шага это его подпапка, для описания курса —
        корень курса. `label` печатается перед каждым сообщением.

        Заодно перезаливает картинки, которые уже на платформе, но локально
        успели измениться (см. `find_stale_media_refs`) — иначе правка схемы
        после выгрузки на Latorn не доезжает.

        Возвращает текст с подставленными адресами; файлы, которых нет на диске
        или которые не удалось загрузить, остаются ссылкой как есть.
        """
        updated = text
        for ref in self.find_local_media_references(text):
            path = folder / ref
            if not path.is_file():
                print(f"{label} файла {ref} нет в папке, ссылка уйдёт как есть")
                continue
            url = self._upload_media_file(path, label)
            if not url:
                continue
            updated = _replace_asset_ref(updated, ref, url)
            print(f"{label} загружен {ref} → {url}")

        for old_url, path in self.find_stale_media_refs(updated, folder):
            url = self._upload_media_file(path, label)
            if not url:
                continue
            updated = _replace_asset_ref(updated, old_url, url)
            print(f"{label} обновлён {path.name} (локальный файл новее) → {url}")
        return updated

    def upload_step_media(self, block: dict[str, Any], folder: Path, step_num: int) -> bool:
        """Грузит локальные медиафайлы шага на Latorn и переписывает ссылки на них.

        Локальный шаг ссылается на файлы по имени (`src="01.png"`). Отправить
        такой текст на платформу мало — картинки там нет, и её приходится
        вставлять руками в веб-редакторе, а он переписывает всю разметку шага:
        оборачивает ячейки таблиц в `<p>`, дописывает ссылкам
        `target="_blank" rel="..."`, добавляет пустой абзац в конец и снимает
        у `<img>` атрибуты `alt` и `name`. Поэтому файлы грузим сами.

        Меняется и текст, который уйдёт на платформу, и локальный файл: иначе
        следующая выгрузка загрузит те же картинки заново, а скачивание покажет
        дифф. Возвращает True, если что-то загрузилось.
        """
        text = block.get("text")
        if not isinstance(text, str) or not text:
            return False
        if not self.find_local_media_references(text) and not self.find_stale_media_refs(
            text, folder
        ):
            return False

        updated = self.upload_media_refs(text, folder, f"    Шаг {step_num}:")
        if updated == text:
            return False

        block["text"] = updated
        save_task_text(folder, updated)
        return True

    def _print_local_asset_warning(self, step_num: int, step_html: str) -> None:
        """Печатает предупреждение про локальные ссылки, которые Latorn не загрузит."""
        asset_refs = self.find_local_asset_references(step_html)
        if not asset_refs:
            return

        preview = ", ".join(asset_refs[:3])
        suffix = "..." if len(asset_refs) > 3 else ""
        print(
            f"    Шаг {step_num}: есть локальные ссылки на файлы "
            f"({preview}{suffix}); они будут отправлены как есть"
        )

    def download_step_media(self, text: str, step_folder: Path) -> None:
        """Скачивает медиа шага (изображения и аудио) в step_folder, не изменяя текст.

        Понимает HTML (`<img src=...>`, `<audio src=...>`, `<source src=...>`)
        и markdown-картинки (`![alt](url)`). Имя файла ищется по порядку:

        1. атрибут `name` тега (`<img name="test124.png">`) — так подписаны
           картинки в шагах, импортированных со Stepik: их URL вида
           https://ucarecdn.stepik.net/<uuid>/-/crop/.../preview/ не содержит
           ни имени, ни расширения. Редактор Latorn атрибут не сохраняет, так
           что после правки шага на платформе он пропадает;
        2. базовое имя пути src, очищенное от служебных суффиксов загрузчика
           Latorn: `/uploads/lesson-media/01-1785082668695-<uuid>.png` → `01.png`
           (у старых загрузок имени в URL нет, там остаётся `<ts>-<uuid>.png`).

        Файл пропускается, если уже существует. Ссылки в тексте не
        переписываются.
        """

        def _attr(tag_attrs: str, attr: str) -> str:
            m = re.search(rf'{attr}=["\']([^"\']*)["\']', tag_attrs, re.IGNORECASE)
            return m.group(1) if m else ""

        def _tags(tag: str) -> list[tuple[str, str]]:
            """Пары (src, name) для всех тегов вида <tag ...> в тексте."""
            return [
                (_attr(m.group(1), "src"), _attr(m.group(1), "name"))
                for m in re.finditer(rf"<{tag}([^>]*)>", text, re.IGNORECASE)
            ]

        images = _tags("img")
        # У markdown-картинок атрибута name нет — имя только из URL
        images += [
            (m.group(1), "") for m in re.finditer(r"!\[[^\]]*\]\(\s*([^)\s]+)", text)
        ]

        # Аудио: src прямо на <audio> или на вложенном <source>
        audios = _tags("audio") + _tags("source")

        # Имя из name часто одинаковое у нескольких картинок шага
        # (Stepik по умолчанию ставит name="image.png"), поэтому имена
        # раздаём один раз на весь шаг: повторы получают суффиксы
        # _2, _3, ... Порядок тегов в тексте стабилен, значит и раскладка
        # имён при повторном скачивании та же.
        assigned: dict[str, str] = {}

        def _resolve(url: str, name: str) -> str:
            # Path(...).name отсекает попытки вылезти из папки шага через name
            filename = Path(sanitize_filename(name)).name
            if not filename:
                filename = _strip_upload_suffix(Path(urlparse(url).path).name)
            if not filename:
                return ""
            stem, dot, ext = filename.rpartition(".")
            if not dot:
                stem, ext = filename, ""
            candidate = filename
            index = 1
            while assigned.get(candidate, url) != url:
                index += 1
                candidate = f"{stem}_{index}{dot}{ext}"
            assigned[candidate] = url
            return candidate

        def _download(url: str, name: str, kind: str, timeout: int) -> None:
            # Качаем только настоящие ресурсы: файлы на Latorn (/uploads/...)
            # и абсолютные URL. Плейсхолдеры вроде "[🎙 Здесь: ...]",
            # локальные черновики и data: — молча пропускаем.
            if not url.startswith(("/uploads/", "http://", "https://", "//")):
                return
            filename = _resolve(url, name)
            if not filename:
                return
            dest = step_folder / filename
            if dest.exists():
                return
            try:
                content = self.client.download_binary(url, timeout=timeout)
            except Exception as exc:
                print(f"WARNING: cannot download {kind} {url}: {exc}")
                return
            # Имя из URL восстанавливается не всегда: у картинок со Stepik,
            # если шаг переcохраняли в редакторе Latorn, остаётся голый uuid.
            # Чтобы такой файл не появлялся заново рядом с переименованным
            # вручную, сверяем содержимое с тем, что уже лежит в папке шага.
            digest = hashlib.md5(content).digest()
            for existing in step_folder.iterdir():
                if not existing.is_file():
                    continue
                if hashlib.md5(existing.read_bytes()).digest() == digest:
                    return
            dest.write_bytes(content)

        for url, name in images:
            _download(url, name, "image", 10)
        # Записи звонков бывают в несколько МБ — таймаут щедрее
        for url, name in audios:
            _download(url, name, "audio", 60)

    def extract_step_name(self, html: str, max_length: int = 100) -> str:
        """Совместимый прокси к выделенной утилите извлечения заголовка шага."""
        return extract_step_name(html, max_length=max_length)

    def save_step_html(self, step_html: str, filepath: Path | str) -> None:
        """Сохраняет HTML шага в файл."""
        Path(filepath).write_text(step_html, encoding=self.config.encoding)
