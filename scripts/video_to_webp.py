r"""Конвертация видео/gif в анимированный WebP под лимит размера (по умолчанию 8 МБ).

Для медиа шагов Latorn: платформа принимает WebP, но не принимает gif и mp4
и не берёт файлы тяжелее ~8 МБ. Скрипт подбирает fps и качество, пока файл
не уложится в лимит, и печатает итоговые параметры. Исходник НЕ удаляется
и НЕ трогается — только читается.

Сама конвертация живёт в `src/latorn_downloader/media_convert.py` — той же,
которой пользуется аплоудер, когда встречает gif или mp4 в папке шага.

Требуется ffmpeg в PATH (libwebp).

Примеры:
  .\.venv\Scripts\python.exe scripts\video_to_webp.py courses\llm_api\01\02\04_text\01.mp4
  .\.venv\Scripts\python.exe scripts\video_to_webp.py in.mp4 out.webp --max-mb 8
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.latorn_downloader.media_convert import (  # noqa: E402
    DEFAULT_MAX_MB,
    ConversionError,
    is_animated,
    to_animated_webp,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Видео/gif → анимированный WebP под лимит размера.")
    parser.add_argument("input", type=Path, help="Исходный файл (mp4/gif/...). Не удаляется.")
    parser.add_argument("output", type=Path, nargs="?", help="Выходной .webp (по умолчанию — рядом с исходником).")
    parser.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB, help="Лимит размера в МБ (по умолчанию 8).")
    args = parser.parse_args()

    src: Path = args.input
    if not src.is_file():
        print(f"Не найден исходник: {src}", file=sys.stderr)
        return 1

    def log(fps: int, quality: int, size: int) -> None:
        print(f"fps={fps} q={quality}: {size / 1024 / 1024:.2f} МБ", file=sys.stderr)

    try:
        dst = to_animated_webp(src, args.output, max_mb=args.max_mb, on_attempt=log)
    except ConversionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    mb = dst.stat().st_size / 1024 / 1024
    anim = "да" if is_animated(dst) else "НЕТ (один кадр!)"
    print(f"Готово: {dst} — {mb:.2f} МБ, анимация: {anim}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
