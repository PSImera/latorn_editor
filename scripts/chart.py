"""Стиль графиков данных для шагов курса: одна панель, прозрачный фон.

График в шаге выглядит как схема курса, а не как дефолтный matplotlib:
прозрачный фон, серый текст #5f6368, серые оси #8a8f98, акценты из палитры
схем, заголовка внутри картинки нет — его роль играет абзац рядом.

    from scripts.chart import panel, BLUE, GREEN

    fig, ax = panel(xlabel="recall", ylabel="precision")
    ax.plot(recall, precision, color=BLUE, linewidth=2.2, marker="o")
    fig.savefig(out, dpi=130, transparent=True)   # без bbox_inches!

Ключевое — поля. `bbox_inches="tight"` обрезает картинку по содержимому:
слева остаётся полоса подписей осей, справа рез идёт вплотную к рамке,
и на центрированной картинке график выглядит съехавшим вправо. Поэтому
поля задаются здесь вручную и одинаковы слева и справа, а фигура
сохраняется без обрезки.
"""

from __future__ import annotations

from typing import Any

# `matplotlib.pyplot` импортируется внутри функций, а не здесь: скрипты шага
# сначала ставят backend `Agg` и только потом трогают pyplot, а импорт этого
# модуля происходит раньше — на уровне файла он бы выбрал backend за них.

# Палитра схем курса: серый текст и линии, три акцента.
TEXT, LINE = "#5f6368", "#8a8f98"
BLUE, GREEN, RED = "#2563eb", "#16a34a", "#dc2626"

RC = {
    "font.family": ["Segoe UI", "Inter", "DejaVu Sans"],
    "text.color": TEXT,
    "axes.labelcolor": TEXT,
    "xtick.color": LINE,
    "ytick.color": LINE,
    "xtick.labelcolor": TEXT,
    "ytick.labelcolor": TEXT,
}

# Поля фигуры 6.4×4.8: слева и справа поровну, по 0.77 дюйма.
MARGINS = {"left": 0.12, "right": 0.88, "top": 0.96, "bottom": 0.13}


def panel(
    xlabel: str | None = None,
    ylabel: str | None = None,
    figsize: tuple[float, float] = (6.4, 4.8),
) -> tuple[Any, Any]:
    """Одна панель в стиле схем курса: прозрачный фон, серые оси, ровные поля."""
    import matplotlib.pyplot as plt

    plt.rcParams.update(RC)
    fig, ax = plt.subplots(figsize=figsize)
    fig.subplots_adjust(**MARGINS)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=14)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=14)
    ax.tick_params(labelsize=12, length=4, width=0.9)
    ax.grid(color=LINE, alpha=0.22, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(LINE)
        ax.spines[side].set_linewidth(0.9)
    return fig, ax


def caption(ax: Any, text: str, fontsize: int = 16, pad: int = 12) -> None:
    """Подпись панели коллажа: серый текст и отступ от кадра.

    `matplotlib` рисует заголовок панели почти чёрным и вплотную к картинке.
    Первое пропадает в тёмной теме, второе выглядит так, будто подпись
    приклеена к кадру, — поэтому цвет и отступ задаются здесь, а не на глаз
    в каждом скрипте.
    """
    ax.set_title(text, fontsize=fontsize, color=TEXT, pad=pad)


def legend(ax: Any, **kwargs: Any) -> Any:
    """Легенда без рамки и с серым текстом — цвет из rcParams туда не доезжает."""
    kwargs.setdefault("fontsize", 13)
    kwargs.setdefault("framealpha", 0.0)
    box = ax.legend(**kwargs)
    for text in box.get_texts():
        text.set_color(TEXT)
    return box
