r"""Конвертация SVG в PNG через headless Edge/Chrome.

Схемы курса уходят на Latorn растром, а SVG-исходник остаётся рядом как
редактируемая копия. Аплоудер делает то же самое сам, когда встречает
`.svg` в папке шага.

Сам рендер живёт в `src/latorn_downloader/media_convert.py`: ffmpeg и лёгкие
python-конвертеры не подходят — essentials-сборка ffmpeg не декодирует SVG,
а cairosvg/svglib не понимают CSS-классы в `<style>`.

Использование:
    python scripts\svg_to_png.py <файл.svg> [ещё.svg ...] [--scale 2] [--bg]

PNG кладётся рядом с исходником с тем же именем (01.svg -> 01.png).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.latorn_downloader.media_convert import ConversionError, svg_to_png  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="SVG -> PNG через headless Edge/Chrome")
    parser.add_argument("svg", nargs="+", type=Path, help="SVG-файлы для конвертации")
    parser.add_argument("--scale", type=float, default=2.0, help="Масштаб от размера viewBox (по умолчанию 2)")
    parser.add_argument("--bg", action="store_true", help="Белый фон вместо прозрачного")
    args = parser.parse_args()

    for svg_path in args.svg:
        if not svg_path.is_file():
            print(f"Файл не найден: {svg_path}", file=sys.stderr)
            return 1
        try:
            out = svg_to_png(svg_path, scale=args.scale, background=args.bg)
        except ConversionError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"{svg_path} -> {out} ({out.stat().st_size} байт)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
