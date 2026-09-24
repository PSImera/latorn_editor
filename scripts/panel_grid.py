"""Сетка панелей для коллажей курса: не больше двух картинок в ряд.

На ширину шага Latorn три и более панели в строку ужимаются так, что кадр
перестаёт читаться. Поэтому любой коллаж из двух и более изображений
раскладывается по два в ряд: 3 панели → 2+1, 5 → 2+2+1 и так далее.
Неполный последний ряд центрируется.

    from scripts.panel_grid import panel_grid

    fig, axes = panel_grid(plt, 5)
    for ax, data in zip(axes, panels):
        ax.imshow(data)
        ax.axis("off")

Помощник нужен только для коллажей. Одиночная картинка, график и схема
рисуются обычным `plt.subplots()`.
"""

from __future__ import annotations

import math
from typing import Any


def panel_grid(
    plt: Any,
    count: int,
    panel_width: float = 5.5,
    panel_height: float = 5.8,
    columns: int = 2,
) -> tuple[Any, list[Any]]:
    """Фигура с `count` панелями по `columns` штук в ряд.

    Возвращает фигуру и список осей в порядке панелей. Последний ряд, если
    он неполный, стоит по центру: для этого сетка режется на вдвое более
    мелкие колонки, и каждая панель занимает две.
    """
    if count < 1:
        raise ValueError("панелей должно быть хотя бы одна")

    rows = math.ceil(count / columns)
    fig = plt.figure(figsize=(panel_width * columns, panel_height * rows))
    grid = fig.add_gridspec(rows, columns * 2)

    axes = []
    for index in range(count):
        row, column = divmod(index, columns)
        in_this_row = min(columns, count - row * columns)
        offset = (columns * 2 - in_this_row * 2) // 2
        start = offset + column * 2
        ax = fig.add_subplot(grid[row, start : start + 2])
        # Картинка с `aspect="equal"` не заполняет ячейку целиком, и при
        # выравнивании по центру запас делится поровну сверху и снизу:
        # подпись следующего ряда встаёт вплотную к кадру предыдущего.
        # Прижимаем кадр к верху ячейки — весь запас уходит вниз, в отступ
        # перед подписью, а сама подпись остаётся у своего кадра.
        ax.set_anchor("N")
        axes.append(ax)
    return fig, axes


def stacked(
    plt: Any,
    images: list[Any],
    panel_width: float = 9.0,
    caption_inches: float = 0.42,
) -> tuple[Any, list[Any]]:
    """Колонка кадров во всю ширину картинки: по одному в ряд, без боковых полей.

    Нужна там, где важны мелкие детали кадра — маски прутков, крапинка на
    поверхности, надрезы по краю. Два кадра в ряд на ширине шага Latorn
    ужимаются вдвое, и деталь пропадает.

    Высота каждого ряда считается по аспекту его картинки, поэтому по бокам
    поля не появляются вовсе, а сверху остаётся ровно столько, сколько нужно
    подписи. Обычная `panel_grid` для этого не годится: она даёт всем рядам
    одинаковую высоту, и кадр другого формата повисает в пустой ячейке.
    """
    if not images:
        raise ValueError("нужна хотя бы одна картинка")

    ratios = [image.shape[0] / image.shape[1] for image in images]
    rows_height = panel_width * sum(ratios)
    figure_height = rows_height + caption_inches * len(images)

    fig = plt.figure(figsize=(panel_width, figure_height))
    hspace = caption_inches / (rows_height / len(images))
    grid = fig.add_gridspec(len(images), 1, height_ratios=ratios, hspace=hspace)
    axes = [fig.add_subplot(grid[row, 0]) for row in range(len(images))]

    top = 1 - caption_inches / figure_height
    fig.subplots_adjust(left=0.0, right=1.0, top=top, bottom=0.0)
    return fig, axes


def row(
    plt: Any,
    images: list[Any],
    panel_width: float = 4.4,
    caption_inches: float = 0.42,
    gap_inches: float = 0.14,
) -> tuple[Any, list[Any]]:
    """Ряд кадров без полей по краям, но с промежутком между панелями.

    Нужна там, где панели сравниваются одним взглядом и мелких деталей в них
    нет: три состояния одной маски, три подсказки на одном кадре. Поля по
    бокам и снизу нулевые — кадры упираются в края картинки, — а между собой
    панели разведены, иначе соседние кадры читаются как один склеенный.

    Ширина фигуры считается с учётом промежутков, поэтому сами кадры остаются
    в полную величину: если просто задать `wspace`, промежуток съест ширину
    осей, и `imshow` ужмёт кадры, оставив пустую полосу снизу.
    """
    if not images:
        raise ValueError("нужна хотя бы одна картинка")

    count = len(images)
    ratios = [image.shape[0] / image.shape[1] for image in images]
    figure_width = panel_width * count + gap_inches * (count - 1)
    figure_height = panel_width * max(ratios) + caption_inches

    fig = plt.figure(figsize=(figure_width, figure_height))
    grid = fig.add_gridspec(1, count, wspace=gap_inches / panel_width)
    axes = [fig.add_subplot(grid[0, column]) for column in range(count)]

    top = 1 - caption_inches / figure_height
    fig.subplots_adjust(left=0.0, right=1.0, top=top, bottom=0.0)
    return fig, axes
