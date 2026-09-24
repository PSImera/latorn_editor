r"""Конвертация локальных медиа шага в форматы, которые принимает Latorn.

`POST /api/uploads/lesson-media` отвечает `VALIDATION_ERROR` на всё, кроме
PNG, JPEG, WebP, SVG, MP3, M4A, OGG, WAV и WebM. То есть анимацию нельзя
класть в шаг ни гифкой, ни MP4 — только анимированным WebP.

Плюс отдельная договорённость про SVG: платформа его принимает, но схемы
курса всё равно уходят растром — так они одинаково выглядят в редакторе,
в мобильном приложении и в теме любого цвета. SVG-исходник остаётся рядом
как редактируемая копия.

Отсюда таблица `CONVERSIONS`: перед загрузкой файл с таким расширением
подменяется соседом в допустимом формате, а исходник не трогается.

Модуль вызывается аплоудером (`local_steps.upload_step_media`) и двумя
скриптами-обёртками — `scripts\video_to_webp.py` и `scripts\svg_to_png.py`.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

# Что во что превращаем перед отправкой на платформу.
CONVERSIONS: dict[str, str] = {".gif": ".webp", ".mp4": ".webp", ".svg": ".png"}

# Лесенка попыток для анимации, от лучшего качества к меньшему размеру: (fps, q:v).
WEBP_LADDER: list[tuple[int, int]] = [
    (18, 90),
    (15, 88),
    (15, 80),
    (12, 85),
    (12, 75),
    (12, 65),
    (10, 60),
    (8, 55),
]

DEFAULT_MAX_MB = 8.0

BROWSERS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
]


class ConversionError(RuntimeError):
    """Файл не удалось привести к формату, который принимает Latorn."""


def find_browser() -> Path:
    """Headless-браузер для рендера SVG.

    ffmpeg и лёгкие python-конвертеры не подходят: essentials-сборка ffmpeg
    не декодирует SVG, а cairosvg/svglib не понимают CSS-классы в `<style>`.
    """
    for path in BROWSERS:
        if path.exists():
            return path
    raise ConversionError("не найден ни Edge, ни Chrome — нечем рендерить SVG")


def svg_size(svg_text: str) -> tuple[float, float]:
    """Размер SVG в px: из viewBox, иначе из width/height."""
    match = re.search(r'viewBox\s*=\s*"([\d.\s,+-]+)"', svg_text)
    if match:
        parts = re.split(r"[\s,]+", match.group(1).strip())
        if len(parts) == 4:
            return float(parts[2]), float(parts[3])

    sizes: list[float] = []
    for attr in ("width", "height"):
        found = re.search(rf'{attr}\s*=\s*"([\d.]+)(px)?"', svg_text)
        if found is None:
            raise ConversionError("в SVG нет ни viewBox, ни числовых width/height")
        sizes.append(float(found.group(1)))
    return sizes[0], sizes[1]


def svg_to_png(svg_path: Path, scale: float = 2.0, background: bool = False) -> Path:
    """Рендерит SVG в PNG рядом с исходником (`01.svg` → `01.png`)."""
    browser = find_browser()
    svg_text = svg_path.read_text(encoding="utf-8")
    width, height = svg_size(svg_text)
    out_path = svg_path.resolve().with_suffix(".png")

    html = (
        '<!doctype html><meta charset="utf-8">'
        "<style>html,body{margin:0;padding:0}svg{display:block;width:100%;height:100%}</style>"
        + svg_text
    )

    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(html, encoding="utf-8")
        colour = "FFFFFFFF" if background else "00000000"
        cmd = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--user-data-dir={Path(tmp) / 'profile'}",
            f"--window-size={round(width * scale)},{round(height * scale)}",
            f"--default-background-color={colour}",
            f"--screenshot={out_path}",
            page.as_uri(),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    if not out_path.exists():
        raise ConversionError(f"браузер не создал {out_path.name}: {result.stderr.strip()[-500:]}")
    return out_path


def _encode_webp(src: Path, dst: Path, fps: int, quality: int) -> None:
    """Один прогон ffmpeg: видео или gif → анимированный WebP."""
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(src),
        "-vf", f"fps={fps}",
        "-c:v", "libwebp",
        "-lossless", "0",
        "-q:v", str(quality),
        "-compression_level", "6",
        "-loop", "0",
        "-an",
        "-vsync", "0",
        str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def is_animated(path: Path) -> bool:
    """Есть ли в WebP чанки анимации (ffprobe местами не читает animated webp)."""
    return b"ANMF" in path.read_bytes()


def to_animated_webp(
    src: Path,
    dst: Path | None = None,
    max_mb: float = DEFAULT_MAX_MB,
    on_attempt: object = None,
) -> Path:
    """Видео или gif → анимированный WebP под лимит размера.

    Идёт по `WEBP_LADDER` от лучшего качества к меньшему, пока файл не
    уложится в `max_mb`. `on_attempt` — необязательный вызываемый объект
    `(fps, quality, size_bytes) -> None` для лога попыток.
    """
    target = dst if dst is not None else src.with_suffix(".webp")
    limit = int(max_mb * 1024 * 1024)

    try:
        for fps, quality in WEBP_LADDER:
            _encode_webp(src, target, fps, quality)
            size = target.stat().st_size
            if callable(on_attempt):
                on_attempt(fps, quality, size)
            if size <= limit:
                return target
    except FileNotFoundError as exc:  # ffmpeg отсутствует в PATH
        raise ConversionError("не найден ffmpeg — нечем собрать анимированный WebP") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()[-500:]
        raise ConversionError(f"ffmpeg не смог собрать WebP: {stderr}") from exc

    raise ConversionError(
        f"не уложились в {max_mb} МБ даже на минимальных настройках — "
        "уменьши разрешение или длительность исходника"
    )


def needs_conversion(path: Path) -> bool:
    """Отклонит ли Latorn файл с таким расширением."""
    return path.suffix.lower() in CONVERSIONS


def convert_for_upload(path: Path, max_mb: float = DEFAULT_MAX_MB) -> Path:
    """Путь к файлу в формате, который принимает Latorn.

    Файл в допустимом формате возвращается как есть. Для `.gif`, `.mp4`
    и `.svg` рядом создаётся (или переиспользуется) сосед в допустимом
    формате; исходник остаётся на месте. Готовый сосед считается годным,
    если он не старше исходника, — так повторная выгрузка не гоняет
    ffmpeg и браузер заново.
    """
    suffix = CONVERSIONS.get(path.suffix.lower())
    if suffix is None:
        return path

    target = path.with_suffix(suffix)
    if target.is_file() and target.stat().st_mtime >= path.stat().st_mtime:
        return target

    if suffix == ".png":
        return svg_to_png(path)
    return to_animated_webp(path, target, max_mb=max_mb)
