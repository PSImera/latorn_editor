r"""Анимация (gif/mp4) → WebP с прозрачным фоном и перекрашенными контурами.

Нужно для чужих учебных анимаций на белом фоне: на Latorn страница шага
перекрашивается под тему, а картинка нет, поэтому белый прямоугольник в
тёмной теме выглядит заплаткой, а чёрные контуры в ней тонут.

Что делает ffmpeg (редактор не нужен):

1. `colorkey` вырезает фоновый цвет — остаётся альфа-канал;
2. `geq` перекрашивает тёмные пиксели в серый цвета схем курса, не трогая
   цветную заливку: условие идёт по самому яркому каналу, поэтому синий
   и бирюзовый (у них максимум канала высокий) остаются как были;
3. `libwebp` собирает анимированный WebP с прозрачностью, без потерь —
   у таких картинок мало цветов, и режим без потерь весит меньше лоссового.

Примеры:
  .\.venv\Scripts\python.exe scripts\anim_to_webp_transparent.py in.gif
  .\.venv\Scripts\python.exe scripts\anim_to_webp_transparent.py in.gif out.webp --threshold 90
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.latorn_downloader.media_convert import is_animated  # noqa: E402

# Серый из палитры схем курса: читается и на белом фоне, и на тёмном.
INK = (138, 143, 152)
# Тёмным считается пиксель, у которого даже самый яркий канал ниже порога.
# 110 оставляет нетронутыми и синий, и бирюзовый solarized-палитры.
THRESHOLD = 110


def build_filter(bg: str, ink: tuple[int, int, int], threshold: int) -> str:
    dark = "max(max(r(X,Y),g(X,Y)),b(X,Y))"
    channels = ":".join(
        f"{name}='if(lt({dark},{threshold}),{value},{name}(X,Y))'"
        for name, value in zip("rgb", ink)
    )
    return f"format=rgba,colorkey={bg}:0.30:0.10,geq={channels}:a='alpha(X,Y)'"


def main() -> int:
    parser = argparse.ArgumentParser(description="Анимация → WebP с прозрачным фоном и серыми контурами.")
    parser.add_argument("input", type=Path, help="Исходный gif/mp4. Не удаляется.")
    parser.add_argument("output", type=Path, nargs="?", help="Выходной .webp (по умолчанию — рядом с исходником).")
    parser.add_argument("--bg", default="0xFFFFFF", help="Цвет фона, который вырезается (по умолчанию белый).")
    parser.add_argument("--threshold", type=int, default=THRESHOLD, help="Порог «тёмного» пикселя, 0–255.")
    args = parser.parse_args()

    src: Path = args.input
    if not src.is_file():
        print(f"Не найден исходник: {src}", file=sys.stderr)
        return 1

    dst: Path = args.output if args.output is not None else src.with_suffix(".webp")
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(src),
        "-vf", build_filter(args.bg, INK, args.threshold),
        "-c:v", "libwebp",
        "-lossless", "1",
        "-loop", "0",
        "-an",
        "-vsync", "0",
        str(dst),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError:
        print("Не найден ffmpeg — нечем собрать WebP.", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print((exc.stderr or b"").decode("utf-8", "replace").strip()[-1000:], file=sys.stderr)
        return 2

    mb = dst.stat().st_size / 1024 / 1024
    anim = "да" if is_animated(dst) else "НЕТ (один кадр!)"
    print(f"Готово: {dst} — {mb:.2f} МБ, фон прозрачный, анимация: {anim}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
