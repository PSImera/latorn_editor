"""
Устанавливает зависимости проекта с нужной CUDA-версией torch.

Использование:
    python scripts/install.py          # авто-определение CUDA
    python scripts/install.py --cpu    # принудительно CPU-версия
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys


TORCH_VERSION = "2.10.0"

# CUDA build → PyTorch index suffix, от новых к старым
CUDA_BUILDS: list[tuple[tuple[int, int], str]] = [
    ((12, 6), "cu126"),
    ((12, 4), "cu124"),
    ((12, 1), "cu121"),
    ((11, 8), "cu118"),
]

PYTORCH_INDEX = "https://download.pytorch.org/whl"


def detect_cuda() -> tuple[int, int] | None:
    """Возвращает (major, minor) из nvcc или None если CUDA не найдена."""
    try:
        out = subprocess.check_output(["nvcc", "--version"], stderr=subprocess.STDOUT, text=True)
        m = re.search(r"release (\d+)\.(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    return None


def pick_build(cuda: tuple[int, int]) -> str:
    """Выбирает ближайший поддерживаемый CUDA-суффикс (не новее установленного)."""
    for version, suffix in CUDA_BUILDS:
        if cuda >= version:
            return suffix
    return "cpu"


def pip(*args: str) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", *args])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu", action="store_true", help="Установить CPU-версию torch")
    args = parser.parse_args()

    if args.cpu:
        build = "cpu"
        print("Режим: CPU")
    else:
        cuda = detect_cuda()
        if cuda:
            build = pick_build(cuda)
            print(f"Обнаружена CUDA {cuda[0]}.{cuda[1]} → используем build {build}")
        else:
            build = "cpu"
            print("CUDA не обнаружена → устанавливаем CPU-версию torch")

    if build == "cpu":
        torch_spec = f"torch=={TORCH_VERSION}"
        pip(torch_spec)
    else:
        index_url = f"{PYTORCH_INDEX}/{build}"
        torch_spec = f"torch=={TORCH_VERSION}+{build}"
        pip(torch_spec, "--extra-index-url", index_url)

    # Остальные зависимости (torch уже стоит, pip пропустит его)
    pip("-r", "requirements.txt")

    print("\nГотово. Проверка:")
    subprocess.check_call([sys.executable, "-c",
        "import torch; print(f'torch {torch.__version__}, CUDA: {torch.cuda.is_available()}')"
    ])


if __name__ == "__main__":
    main()
