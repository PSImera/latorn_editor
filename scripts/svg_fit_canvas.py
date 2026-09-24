r"""Выравнивание полей SVG-схемы: одинаковый отступ со всех четырёх сторон.

Поля на глаз не сходятся: холст задаётся руками, а реальные границы рисунка
зависят от толщины обводок, наконечников стрелок и метрик шрифта. Скрипт
рендерит схему headless-браузером, находит настоящий bbox непрозрачных
пикселей и переписывает viewBox так, чтобы отступ до края был одинаковым
сверху, снизу, слева и справа.

Использование:
    python scripts\svg_fit_canvas.py <файл.svg> [ещё.svg ...] [--padding 24] [--check]

По умолчанию viewBox переписывается и PNG перерисовывается рядом.
С --check файлы не меняются, печатается только текущий перекос полей.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.latorn_downloader.media_convert import find_browser, svg_to_png  # noqa: E402

VIEWBOX_RE = re.compile(r'viewBox\s*=\s*"([\d.\s,+-]+)"')

PROBE_MARGIN = 64.0
PROBE_SCALE = 4.0
ALPHA_THRESHOLD = 8


def parse_viewbox(svg_text: str) -> tuple[float, float, float, float]:
    m = VIEWBOX_RE.search(svg_text)
    if m is None:
        sys.exit("В SVG нет viewBox — нечего выравнивать.")
    parts = re.split(r"[\s,]+", m.group(1).strip())
    if len(parts) != 4:
        sys.exit(f"Неожиданный viewBox: {m.group(1)!r}")
    return tuple(float(p) for p in parts)  # type: ignore[return-value]


def set_viewbox(svg_text: str, box: tuple[float, float, float, float]) -> str:
    value = " ".join(f"{v:g}" for v in box)
    return VIEWBOX_RE.sub(f'viewBox="{value}"', svg_text, count=1)


def render_probe(browser: Path, svg_text: str, width: int, height: int) -> Image.Image:
    """Рендер SVG в RGBA на прозрачном фоне с запасом по краям."""
    html = (
        '<!doctype html><meta charset="utf-8">'
        "<style>html,body{margin:0;padding:0}svg{display:block;width:100%;height:100%}</style>"
        + svg_text
    )
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(html, encoding="utf-8")
        shot = Path(tmp) / "probe.png"
        cmd = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--user-data-dir={Path(tmp) / 'profile'}",
            f"--window-size={width},{height}",
            "--default-background-color=00000000",
            f"--screenshot={shot}",
            page.as_uri(),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if not shot.exists():
            sys.exit(f"Браузер не отрендерил пробу.\nstderr: {result.stderr.strip()[-2000:]}")
        return Image.open(shot).convert("RGBA").copy()


def ink_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    alpha = image.getchannel("A").point(lambda a: 255 if a > ALPHA_THRESHOLD else 0)
    box = alpha.getbbox()
    if box is None:
        sys.exit("В схеме не найдено ни одного непрозрачного пикселя.")
    return box


def measure(browser: Path, svg_text: str) -> tuple[float, float, float, float]:
    """Границы рисунка в координатах viewBox: (x0, y0, x1, y1)."""
    min_x, min_y, width, height = parse_viewbox(svg_text)
    probe_box = (
        min_x - PROBE_MARGIN,
        min_y - PROBE_MARGIN,
        width + 2 * PROBE_MARGIN,
        height + 2 * PROBE_MARGIN,
    )
    probe_text = set_viewbox(svg_text, probe_box)
    image = render_probe(
        browser,
        probe_text,
        round(probe_box[2] * PROBE_SCALE),
        round(probe_box[3] * PROBE_SCALE),
    )
    left, top, right, bottom = ink_bbox(image)
    return (
        probe_box[0] + left / PROBE_SCALE,
        probe_box[1] + top / PROBE_SCALE,
        probe_box[0] + right / PROBE_SCALE,
        probe_box[1] + bottom / PROBE_SCALE,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Одинаковые поля по краям SVG-схемы")
    parser.add_argument("svg", nargs="+", type=Path, help="SVG-файлы")
    parser.add_argument("--padding", type=float, default=24.0, help="Поле со всех сторон (по умолчанию 24)")
    parser.add_argument("--check", action="store_true", help="Только показать текущие поля, не менять файлы")
    parser.add_argument("--scale", type=float, default=2.0, help="Масштаб PNG (по умолчанию 2)")
    args = parser.parse_args()

    browser = find_browser()
    pad = args.padding

    for svg_path in args.svg:
        if not svg_path.exists():
            sys.exit(f"Файл не найден: {svg_path}")
        text = svg_path.read_text(encoding="utf-8")
        min_x, min_y, width, height = parse_viewbox(text)
        x0, y0, x1, y1 = measure(browser, text)

        left = x0 - min_x
        top = y0 - min_y
        right = (min_x + width) - x1
        bottom = (min_y + height) - y1
        print(
            f"{svg_path}: поля сейчас "
            f"слева {left:.1f}, справа {right:.1f}, сверху {top:.1f}, снизу {bottom:.1f}"
        )

        if args.check:
            continue

        new_x = round(x0 - pad)
        new_y = round(y0 - pad)
        new_box = (new_x, new_y, round(x1 + pad) - new_x, round(y1 + pad) - new_y)
        if (min_x, min_y, width, height) == new_box:
            print("  уже выровнено")
            continue

        svg_path.write_text(set_viewbox(text, new_box), encoding="utf-8")
        out = svg_to_png(svg_path, scale=args.scale, background=False)
        print(f"  viewBox -> {' '.join(f'{v:g}' for v in new_box)}; {out.name} перерисован")


if __name__ == "__main__":
    main()
