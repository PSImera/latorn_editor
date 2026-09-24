"""Читаемое форматирование HTML-текста шага.

Веб-редактор Latorn сохраняет содержимое шага одной строкой: после правки
`<h1>Заголовок</h1><p>Первый абзац</p><p>Второй…` — читать и править такой
файл невозможно. Шаги, импортированные со Stepik, наоборот, приходят уже
разложенными по строкам — их форматтер не трогает. Схлопнутый текст он
раскладывает в том же стиле, в каком лежит большая часть курсов:

- каждый блочный элемент начинается с новой строки, вложенность — табами;
- между блоками верхнего уровня — пустая строка;
- элемент, внутри которого только инлайновое содержимое, остаётся одной
  строкой целиком (`<li>Текст со <strong>вставками</strong></li>`);
- содержимое `<pre>` не трогается вообще;
- пустой абзац в конце (`<p></p>`), который дописывает веб-редактор Latorn
  при каждом сохранении шага, срезается — он ничего не отображает, а дифф
  после скачивания даёт.

Функция идемпотентна: уже отформатированный текст возвращается без изменений.
Пробелы между блочными тегами на отображение не влияют, поэтому выгрузка
отформатированного текста обратно на платформу для студента ничего не меняет.
"""

from __future__ import annotations

import re

# Блочные элементы: каждый начинается с новой строки. Всё остальное
# (strong, em, code, a, span, br…) считается инлайновым и не разрывается.
# Медиа (img, audio, video, iframe) намеренно не в списке: внутри абзаца
# картинку рвать незачем, а отдельно стоящая всё равно попадёт на свою строку —
# она оказывается между границами соседних блоков.
BLOCK_TAGS = frozenset(
    """
    address article aside blockquote details div dl dd dt figcaption figure
    footer form h1 h2 h3 h4 h5 h6 header hr li main nav ol p pre section
    summary table tbody td tfoot th thead tr ul
    """.split()
)

# Пустые элементы: закрывающего тега у них нет.
VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)

# Внутри этих элементов значим каждый пробел — содержимое не трогаем.
RAW_TAGS = ("pre", "textarea", "script", "style")

# Элементы, содержимое которых не получает дополнительного отступа. Так
# разложены списки во всех курсах, приехавших со Stepik: <li> и вложенный в
# него <p> стоят на одном уровне. Расходиться с этим незачем — иначе форматтер
# перетряхивает файлы, в которых ничего не менялось.
FLAT_CHILDREN_TAGS = frozenset({"li"})

_TOKEN_RE = re.compile(r"<(?P<closing>/?)(?P<tag>[a-zA-Z][a-zA-Z0-9]*)(?P<attrs>[^>]*)>")

# Пустой абзац в конце шага — след веб-редактора Latorn: он дописывает <p></p>
# при каждом сохранении. На отображение это не влияет, а дифф после скачивания
# даёт. Срезаем — и в даунлодере, и в хуке форматирования.
_TRAILING_EMPTY_P_RE = re.compile(
    r"(?:\s*<p>(?:\s|&nbsp;|<br\s*/?>)*</p>)+\s*$", re.IGNORECASE
)


def _raw_inner_spans(text: str) -> list[tuple[int, int]]:
    """Границы содержимого `<pre>…</pre>` и подобных — без самих тегов."""
    spans: list[tuple[int, int]] = []
    for tag in RAW_TAGS:
        pattern = rf"<{tag}\b[^>]*>(?P<inner>.*?)</{tag}\s*>"
        for m in re.finditer(pattern, text, re.IGNORECASE | re.DOTALL):
            spans.append(m.span("inner"))
    return spans


def is_laid_out(text: str) -> bool:
    """Разложен ли текст по строкам — есть ли перевод строки между тегами.

    Переводы внутри `<pre>` не в счёт: код многострочный и в схлопнутом тексте.
    """
    without_raw = text
    for lo, hi in sorted(_raw_inner_spans(text), reverse=True):
        without_raw = without_raw[:lo] + without_raw[hi:]
    return re.search(r">\s*\n\s*<", without_raw) is not None


def format_step_html(text: str) -> str:
    """Раскладывает HTML шага по строкам. Markdown сюда передавать нельзя.

    Текст, который уже разложен по строкам, возвращается как есть. Курсы,
    приехавшие со Stepik, размечены сериализатором с его собственными
    привычками (отступы у вложенных списков, пустые строки в неочевидных
    местах), и переразмечать такие файлы по-своему — значит получать дифф там,
    где содержимое не менялось. Форматтер нужен для другого: редактор Latorn
    отдаёт правленый шаг одной строкой, вот её он и раскладывает.
    """
    text = _TRAILING_EMPTY_P_RE.sub("", text.replace("\r\n", "\n").strip()).strip()
    if not text or is_laid_out(text):
        return text

    protected = _raw_inner_spans(text)

    def is_protected(start: int) -> bool:
        return any(lo <= start < hi for lo, hi in protected)

    lines: list[str] = []
    buffer = ""  # содержимое текущей строки
    buffer_depth = 0
    depth = 0
    open_depths: list[int] = []  # глубина каждого незакрытого блока

    def emit(line: str, indent: int) -> None:
        if lines and indent == 0 and not line.startswith("</"):
            lines.append("")  # пустая строка между блоками верхнего уровня
        lines.append("\t" * indent + line)

    def flush() -> None:
        nonlocal buffer, buffer_depth
        if buffer.strip():
            emit(buffer.strip(), buffer_depth)
        buffer = ""
        buffer_depth = depth

    pos = 0
    for m in _TOKEN_RE.finditer(text):
        if is_protected(m.start()):
            continue
        tag = m.group("tag").lower()
        if tag not in BLOCK_TAGS:
            continue

        buffer += text[pos : m.start()]
        pos = m.end()

        if m.group("closing"):
            closing_depth = open_depths.pop() if open_depths else max(depth - 1, 0)
            if buffer.strip():
                # в строке уже что-то есть — закрывающий тег дописываем туда же
                buffer += m.group(0)
                depth = closing_depth
                flush()
            else:
                buffer = ""
                depth = closing_depth
                emit(m.group(0), depth)
                buffer_depth = depth
        else:
            flush()
            if tag in VOID_TAGS or m.group("attrs").rstrip().endswith("/"):
                emit(m.group(0), depth)
            else:
                buffer = m.group(0)
                buffer_depth = depth
                open_depths.append(depth)
                if tag not in FLAT_CHILDREN_TAGS:
                    depth += 1

    buffer += text[pos:]
    flush()

    return "\n".join(lines).strip()
