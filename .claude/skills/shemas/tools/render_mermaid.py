from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def resolve_mmdc(repo_root: Path) -> list[str]:
    local_cmd = repo_root / "node_modules" / ".bin" / "mmdc.cmd"
    local_sh = repo_root / "node_modules" / ".bin" / "mmdc"
    if local_cmd.exists():
        return [str(local_cmd)]
    if local_sh.exists():
        return [str(local_sh)]
    if shutil.which("mmdc"):
        return ["mmdc"]
    if shutil.which("npx"):
        return ["npx", "-p", "@mermaid-js/mermaid-cli", "mmdc"]
    raise FileNotFoundError(
        "Не найден mmdc. Установите @mermaid-js/mermaid-cli локально в проекте "
        "или убедитесь, что npx доступен в PATH."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Рендер Mermaid .mmd в SVG по проектному стилю курсов."
    )
    parser.add_argument("--input", required=True, help="Путь к исходному .mmd файлу.")
    parser.add_argument(
        "--output",
        help="Путь к выходному .svg файлу. По умолчанию рядом с input с тем же именем.",
    )
    parser.add_argument(
        "--config",
        help="Путь к JSON-конфигу Mermaid. По умолчанию tools/latorn_mermaid_config.json.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=680,
        help="Базовая ширина рендера. По умолчанию 680.",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Масштаб рендера для mmdc. По умолчанию 1.0.",
    )
    return parser


def main() -> int:
    repo_root = resolve_repo_root()
    parser = build_parser()
    args = parser.parse_args()

    input_path = (repo_root / args.input).resolve() if not Path(args.input).is_absolute() else Path(args.input)
    if not input_path.exists():
        parser.error(f"Файл Mermaid не найден: {input_path}")

    output_path = (
        (repo_root / args.output).resolve()
        if args.output and not Path(args.output).is_absolute()
        else Path(args.output).resolve()
        if args.output
        else input_path.with_suffix(".svg")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    default_config = Path(__file__).resolve().parent / "latorn_mermaid_config.json"
    config_path = (
        (repo_root / args.config).resolve()
        if args.config and not Path(args.config).is_absolute()
        else Path(args.config).resolve()
        if args.config
        else default_config
    )
    if not config_path.exists():
        parser.error(f"Конфиг Mermaid не найден: {config_path}")

    try:
        cmd = resolve_mmdc(repo_root)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    render_cmd = cmd + [
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--configFile",
        str(config_path),
        "--backgroundColor",
        "transparent",
        "--width",
        str(args.width),
        "--scale",
        str(args.scale),
    ]

    completed = subprocess.run(
        render_cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout, file=sys.stderr)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr)
        return completed.returncode

    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
