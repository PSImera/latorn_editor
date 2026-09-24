"""Отчёт об окружении стенда: железо, версии, доступность CUDA.

Запускать интерпретатором песочницы:

    .\\.venv\\Scripts\\python.exe ..\\latorn_editor\\scripts\\env_report.py

Печатает ровно то, что потом идёт в урок про окружение и в подписи к числам
курса («получено на такой-то карте, такие-то версии»). Ничего не выдумывает:
если пакета нет — так и пишет.
"""

from __future__ import annotations

import importlib
import platform
import shutil
import subprocess
import sys

PACKAGES = [
    "torch",
    "torchvision",
    "ultralytics",
    "cv2",
    "numpy",
    "scipy",
    "PIL",
    "albumentations",
    "supervision",
    "torchmetrics",
    "pycocotools",
    "faster_coco_eval",
    "lap",
    "timm",
    "pandas",
    "matplotlib",
    "av",
    "onnx",
    "onnxruntime",
    "openvino",
    "fiftyone",
]


def version_of(name: str) -> str:
    try:
        module = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 — интересна любая причина
        return f"нет ({type(exc).__name__})"
    return str(getattr(module, "__version__", "версия не объявлена"))


def hardware() -> None:
    print("== Железо ==")
    print(f"ОС           : {platform.platform()}")
    print(f"Python       : {sys.version.split()[0]}  ({sys.executable})")

    try:
        import cpuinfo

        print(f"Процессор    : {cpuinfo.get_cpu_info()['brand_raw']}")
    except Exception:
        print(f"Процессор    : {platform.processor()}")

    try:
        import psutil

        print(f"ОЗУ          : {psutil.virtual_memory().total / 1024**3:.1f} ГБ")
    except Exception:
        print("ОЗУ          : psutil не установлен")

    if shutil.which("nvidia-smi"):
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        print(f"Видеокарта   : {out.stdout.strip() or 'nvidia-smi ничего не вернул'}")
    else:
        print("Видеокарта   : nvidia-smi не найден")


def torch_report() -> None:
    print("\n== PyTorch ==")
    try:
        import torch
    except ImportError:
        print("torch не установлен")
        return

    print(f"torch        : {torch.__version__}")
    print(f"сборка CUDA  : {torch.version.cuda or 'без CUDA (CPU-сборка)'}")
    print(f"cuda.is_available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        print(f"устройство   : {props.name}")
        print(f"VRAM         : {props.total_memory / 1024**3:.1f} ГБ")
        print(f"compute      : sm_{props.major}{props.minor}")
        print(f"cuDNN        : {torch.backends.cudnn.version()}")


def packages_report() -> None:
    print("\n== Версии пакетов ==")
    width = max(len(name) for name in PACKAGES)
    for name in PACKAGES:
        print(f"{name:<{width}} : {version_of(name)}")


if __name__ == "__main__":
    hardware()
    torch_report()
    packages_report()
