"""Обложка курса: коллаж 3:2 из пяти панелей.

Верхний ряд — два прямоугольника 3:2 (детекция на полке, схема residual-блока),
нижний — три квадрата (OCR на чеке, карта внимания, веса первых свёрток).
Панели встык, без отступов.

Схема residual-блока для обложки перерисована на тёмном фоне из
`courses/computer-vision/01/02/02_text/04.svg` — в шаге она остаётся светлой.

Четыре панели берутся прямо из шагов курса, пятая (веса первых свёрток) —
из `scripts/_cover_build/conv1_filters.png`: она была снята в песочнице,
которой на этой машине уже нет, поэтому лежит рядом со скриптом.

    .\\.venv\\Scripts\\python.exe scripts\\make_cover.py

Результат: `courses/computer-vision/cover.png` (1800x1200).
Обложку аплоудер не отправляет — её загружают на latorn.ru вручную.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.latorn_downloader.media_convert import svg_to_png  # noqa: E402

COURSE = ROOT / "courses" / "computer-vision"
BUILD = Path(__file__).resolve().parent / "_cover_build"
OUT = COURSE / "cover.png"

S = 600  # сторона квадратной плитки
W, H = S * 3, S * 2  # 1800x1200
BG = "white"

# тёмный вариант схемы residual-блока: viewBox расширен по горизонтали до 3:2
DARK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="68.75 5 478.5 319" width="100%">
\t<defs>
\t\t<style>
\t\t\t.c-neutral rect { fill: #1b2436; stroke: #35435c; }
\t\t\t.ts   { fill: #d3dbe6; font-family: Inter, "Segoe UI", Arial, sans-serif; }
\t\t\t.arr  { stroke: #9aa6b8; stroke-width: 1.25; fill: none; }
\t\t\t.skip { stroke: #60a5fa; stroke-width: 1.5; fill: none; }
\t\t\t.sum  { fill: #1b2436; stroke: #9aa6b8; stroke-width: 1.25; }
\t\t</style>
\t\t<marker id="arrow" viewBox="0 0 10 7" refX="10" refY="3.5" markerWidth="7" markerHeight="5" orient="auto">
\t\t\t<path d="M0,0 L10,3.5 L0,7 z" fill="#9aa6b8"/>
\t\t</marker>
\t\t<marker id="arrowblue" viewBox="0 0 10 7" refX="10" refY="3.5" markerWidth="7" markerHeight="5" orient="auto">
\t\t\t<path d="M0,0 L10,3.5 L0,7 z" fill="#60a5fa"/>
\t\t</marker>
\t</defs>

\t<rect x="68.75" y="5" width="478.5" height="319" fill="#0b1220"/>

\t<text class="ts" x="264" y="36" font-size="13" font-weight="600" text-anchor="middle">x</text>

\t<path class="arr" d="M 264 44 L 264 68" marker-end="url(#arrow)"/>

\t<g class="c-neutral">
\t\t<rect x="164" y="72" width="200" height="48" rx="10"/>
\t\t<rect x="164" y="168" width="200" height="48" rx="10"/>
\t</g>
\t<text class="ts" x="264" y="101" font-size="12" font-weight="500" text-anchor="middle">слой весов</text>
\t<text class="ts" x="264" y="197" font-size="12" font-weight="500" text-anchor="middle">слой весов</text>

\t<path class="arr" d="M 264 120 L 264 164" marker-end="url(#arrow)"/>
\t<text class="ts" x="276" y="147" font-size="11">relu</text>

\t<path class="arr" d="M 264 216 L 264 244" marker-end="url(#arrow)"/>

\t<circle class="sum" cx="264" cy="260" r="14"/>
\t<text class="ts" x="264" y="265" font-size="13" text-anchor="middle">+</text>

\t<path class="arr" d="M 264 274 L 264 300" marker-end="url(#arrow)"/>
\t<text class="ts" x="276" y="292" font-size="11">relu</text>

\t<path class="skip" d="M 264 52 L 456 52 L 456 260 L 282 260" marker-end="url(#arrowblue)"/>
\t<text class="ts" x="468" y="152" font-size="12" font-weight="500">identity</text>

\t<text class="ts" x="148" y="148" font-size="12" font-weight="500" text-anchor="end">F(x)</text>
\t<text class="ts" x="148" y="264" font-size="12" font-weight="500" text-anchor="end">F(x) + x</text>
</svg>
"""


def piece(
    path: Path, box: tuple[int, int, int, int] | None, size: tuple[int, int]
) -> Image.Image:
    src = Image.open(path)
    if src.mode in ("RGBA", "LA", "P"):
        src = src.convert("RGBA")
        src = Image.alpha_composite(Image.new("RGBA", src.size, BG), src)
    img = src.convert("RGB")
    if box is not None:
        img = img.crop(box)
    return img.resize(size, Image.LANCZOS)


def build_residual_dark() -> Path:
    BUILD.mkdir(exist_ok=True)
    svg = BUILD / "residual_dark.svg"
    svg.write_text(DARK_SVG, encoding="utf-8")
    return svg_to_png(svg, scale=3.0, background=True)


def main() -> None:
    residual = build_residual_dark()
    panels = [
        # верхний ряд: два прямоугольника 900x600
        ((0, 0), piece(COURSE / "01/03/01_text/01.png", (13, 60, 699, 517), (S * 3 // 2, S))),
        ((S * 3 // 2, 0), piece(residual, None, (S * 3 // 2, S))),
        # нижний ряд: три квадрата 600x600
        ((0, S), piece(COURSE / "01/01/06_text/03.png", (40, 278, 410, 648), (S, S))),
        ((S, S), piece(COURSE / "01/02/04_text/02.png", (397, 689, 843, 1136), (S, S))),
        (
            (S * 2, S),
            piece(BUILD / "conv1_filters.png", None, (S, S)),
        ),
    ]

    canvas = Image.new("RGB", (W, H), BG)
    for pos, img in panels:
        canvas.paste(img, pos)
    canvas.save(OUT)
    print(OUT, canvas.size)


if __name__ == "__main__":
    main()
