"""Поля вокруг скриншота под цвет его собственного фона.

Скриншот, обрезанный вплотную к содержимому, на странице урока выглядит
придавленным: текст упирается в край картинки. Скрипт определяет цвет фона
по рамке пикселей вдоль периметра и дорисовывает поля этим же цветом —
шва не видно, потому что цвет берётся из самого кадра.

Запуск (интерпретатором редактора, из корня репозитория):
    .\\.venv\\Scripts\\python.exe scripts\\pad_image.py <path.png> [--padding 32] [--check]

Без `--out` файл перезаписывается на месте. `--check` только печатает
найденный цвет фона и предполагаемый размер, ничего не сохраняя.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from PIL import Image

Color = tuple[int, int, int, int]


def border_pixels(image: Image.Image, thickness: int = 2) -> list[Color]:
    """Пиксели рамки шириной `thickness` вдоль всех четырёх краёв."""
    width, height = image.size
    pixels = image.load()
    if pixels is None:
        raise SystemExit("не удалось прочитать пиксели изображения")

    out: list[Color] = []
    for y in range(min(thickness, height)):
        for x in range(width):
            out.append(pixels[x, y])
            out.append(pixels[x, height - 1 - y])
    for x in range(min(thickness, width)):
        for y in range(height):
            out.append(pixels[x, y])
            out.append(pixels[width - 1 - x, y])
    return out


def background_color(image: Image.Image) -> Color:
    """Самый частый цвет по периметру — он и есть фон скриншота."""
    counter = Counter(border_pixels(image))
    color, hits = counter.most_common(1)[0]
    total = sum(counter.values())
    share = hits / total
    if share < 0.5:
        print(f"⚠️ фон по краю неоднороден: самый частый цвет занимает {share:.0%}")
    return color


def pad(image: Image.Image, padding: int, color: Color) -> Image.Image:
    width, height = image.size
    canvas = Image.new("RGBA", (width + padding * 2, height + padding * 2), color)
    canvas.paste(image, (padding, padding))
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="файл изображения")
    parser.add_argument("--padding", type=int, default=32, help="поле в пикселях (по умолчанию 32)")
    parser.add_argument("--out", type=Path, default=None, help="куда сохранить; по умолчанию на месте")
    parser.add_argument("--check", action="store_true", help="только показать цвет фона")
    args = parser.parse_args()

    if not args.path.exists():
        raise SystemExit(f"нет файла {args.path}")

    image = Image.open(args.path).convert("RGBA")
    color = background_color(image)
    hex_color = "#{:02x}{:02x}{:02x}".format(*color[:3])
    width, height = image.size

    if args.check:
        print(f"{args.path}: {width}×{height}, фон {hex_color}, поле добавит по {args.padding} px")
        return

    result = pad(image, args.padding, color)
    target = args.out or args.path
    result.save(target)
    print(f"{target}: {width}×{height} → {result.width}×{result.height}, поля {args.padding} px цветом {hex_color}")


if __name__ == "__main__":
    main()
