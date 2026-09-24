"""Смоук стенда: проверяет, что набор действительно работает, а не просто ставится.

Пять проверок — по одной на каждый кусок, на котором стоит курс:
матричные операции на видеокарте, OpenCV, инференс детектора, аугментации,
метрика из библиотеки. Каждая печатает измеренное значение, а не «OK».

Запускать интерпретатором песочницы, из её корня:

    .\\.venv\\Scripts\\python.exe ..\\latorn_editor\\scripts\\smoke.py

Загружает веса `yolo11n.pt` (~5 МБ) и тестовый кадр Ultralytics при первом
запуске — нужна сеть. Веса кладутся в `runs/weights/` песочницы.

Пути берутся от текущей рабочей директории: скрипт живёт в общем `scripts/`
репозитория, а веса и кадр кладёт туда, откуда его запустили.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

# Скрипт лежит в общем `scripts/`, а проверяет ту песочницу, из которой его
# запустили, — поэтому корень берётся от рабочей директории, а не от файла.
SANDBOX = Path.cwd()
WEIGHTS = SANDBOX / "runs" / "weights"


def check_torch() -> None:
    import torch

    print("== torch ==")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"устройство: {device}")

    a = torch.randn(4096, 4096, device=device)
    b = torch.randn(4096, 4096, device=device)
    torch.mm(a, b)  # прогрев: первый запуск включает инициализацию контекста
    if device == "cuda":
        torch.cuda.synchronize()

    start = time.perf_counter()
    for _ in range(10):
        torch.mm(a, b)
    if device == "cuda":
        torch.cuda.synchronize()
    elapsed = time.perf_counter() - start

    tflops = 10 * 2 * 4096**3 / elapsed / 1e12
    print(f"matmul 4096x4096 x10: {elapsed:.3f} с  ({tflops:.1f} TFLOPS, fp32)")
    if device == "cuda":
        print(f"пик памяти: {torch.cuda.max_memory_allocated() / 1024**2:.0f} МБ")


def check_opencv() -> None:
    import cv2

    print("\n== opencv ==")
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(image, (100, 100), (300, 400), (0, 255, 0), 3)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    print(f"версия: {cv2.__version__}")
    print(f"кадр {image.shape}, ненулевых пикселей после Canny: {int((edges > 0).sum())}")


def check_ultralytics() -> tuple[np.ndarray, np.ndarray] | None:
    from ultralytics import YOLO

    print("\n== ultralytics ==")
    WEIGHTS.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(WEIGHTS / "yolo11n.pt"))

    start = time.perf_counter()
    results = model.predict(
        "https://ultralytics.com/images/bus.jpg", verbose=False, device=0
    )
    elapsed = time.perf_counter() - start

    result = results[0]
    names = result.names
    counts: dict[str, int] = {}
    for cls in result.boxes.cls.tolist():
        label = names[int(cls)]
        counts[label] = counts.get(label, 0) + 1

    print(f"кадр {result.orig_shape}, объектов: {len(result.boxes)}")
    print(f"по классам: {counts}")
    print(f"предсказание вместе с загрузкой кадра: {elapsed:.2f} с")
    print(f"скорость по замеру ultralytics: {result.speed}")

    out = SANDBOX / "runs" / "smoke_bus.jpg"
    result.save(filename=str(out))
    print(f"кадр с рамками: {out.relative_to(SANDBOX)}")
    return result.orig_img, result.boxes.xyxy.cpu().numpy()


def check_albumentations(image: np.ndarray) -> None:
    import albumentations as A

    print("\n== albumentations ==")
    transform = A.Compose(
        [
            A.HorizontalFlip(p=1.0),
            A.RandomBrightnessContrast(brightness_limit=0.3, contrast_limit=0.3, p=1.0),
            A.Resize(640, 640),
        ]
    )
    augmented = transform(image=image)["image"]
    print(f"версия: {A.__version__}")
    print(f"{image.shape} -> {augmented.shape}, средняя яркость {image.mean():.1f} -> {augmented.mean():.1f}")


def check_metrics() -> None:
    import torch
    from torchvision.ops import box_iou

    print("\n== метрики ==")
    a = torch.tensor([[0.0, 0.0, 10.0, 10.0]])
    b = torch.tensor([[5.0, 5.0, 15.0, 15.0]])
    library = float(box_iou(a, b))

    # то же руками — так задача с автопроверкой и сверяется с эталоном
    inter = max(0.0, min(10, 15) - max(0, 5)) * max(0.0, min(10, 15) - max(0, 5))
    manual = inter / (100 + 100 - inter)
    print(f"torchvision.box_iou: {library:.6f}")
    print(f"руками:              {manual:.6f}")
    print(f"расхождение:         {abs(library - manual):.2e}")


if __name__ == "__main__":
    check_torch()
    check_opencv()
    detection = check_ultralytics()
    if detection is not None:
        check_albumentations(detection[0])
    check_metrics()
    print("\nвсе проверки прошли")
