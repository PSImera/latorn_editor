"""Проверка шагов урока на запреты /public-materials и на канон файлов.

Правила словами уже записаны в `CLAUDE.md` и в скиллах, но при генерации они
просачиваются мимо: «живут», заголовок «Итоги», анонс следующего урока. Этот
скрипт превращает их в проверяемый список — прогоняется после генерации урока
и перед тем, как считать урок готовым.

Запуск (интерпретатором редактора, из корня репозитория):
    .\\.venv\\Scripts\\python.exe scripts\\check_lesson_style.py courses\\<курс>\\<модуль>\\<урок>

Кроме запретов стиля проверяется ширина блоков кода: строка, которая не влезает
в колонку шага, включает горизонтальную прокрутку и прячет свой конец — а там
обычно и стоит то, ради чего строка написана.

Можно указать и отдельный файл. Код возврата 1, если найдено хоть одно
нарушение уровня «ошибка».
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

Rule = tuple[str, str, str]

# (уровень, название, регулярное выражение)
RULES: list[Rule] = [
    (
        "ошибка",
        "очеловечивание кода и систем",
        r"жив[её]т|живут|\bжив\b|живой|посели(ть|л)|хочет|хотят|любит|не любит|думает"
        # Границы слова обязательны: «вы сидите за машиной» — про человека, это законно.
        r"|вознагражда\w+|ведёт себя|\bсидит\b|\bсидят\b|\bторчит\b|\bторчат\b"
        r"|\bкрутится\b|\bкрутятся\b|засел|уста(ёт|л)|просыпается|засыпает",
    ),
    (
        "ошибка",
        "очеловечивание — разговор с неодушевлённым",
        # «спросить у набора», «данные говорят», «компилятор ругается». Собеседником
        # становится то, что собеседником быть не может. У людей спрашивать законно,
        # поэтому список адресатов перечислен явно. Языковой модели вопрос задают
        # буквально — её в списке нет намеренно.
        r"(спрос|поговор|побеседов|расспрос)\w*\s+(у\s+|с\s+)?"
        r"(набор\w*|данн\w+|систем\w+|код\w*|таблиц\w+|баз[ыу]\b|индекс\w*|график\w*|логов?\b|разметк\w+)"
        # «вопрос, который стоит задавать набору» — тот же оборот другими словами.
        r"|(задава\w+|задат\w+|адресова\w+)\s+(набору|данным|системе|коду|таблице|базе|графику|разметке)"
        r"|вопрос\w*\s+(к\s+)?(набору|данным|системе|коду|таблице|базе|графику|разметке)"
        # «метрика отвечает на вопрос заказчика» — устойчивый оборот, не оживление,
        # поэтому метрики среди подлежащих нет.
        r"|(набор|данные|система|разметка|график|таблица|код|лог|логи|цифры|числа"
        r"|компилятор|линтер|тест|тесты|сборка|скрипт|библиотека)\w*\s+"
        r"(говор(ит|ят)|отвеча(ет|ют)|подсказыва(ет|ют)|расскаж(ет|ут)|скаж(ет|ут)|шепч\w+"
        r"|жалу(ется|ются)|руга(ется|ются)|молчит|спорит|намекает)",
    ),
    (
        "ошибка",
        "разговорная метафора перемещения",
        # «поедет в архив», «уехало в экспорт», «доезжает до всех» — данные и правки
        # не ездят. Голое «едет» не ловим: на видео машина «стоит или едет» — это буквально.
        r"поед(ет|ут|ем)|уезжа(ет|ют)|уеха(л|ло|ли)|доезжа(ет|ют)|доеха(л|ло|ли)"
        r"|приеха(л|ло|ли)|укати(т|лся)|улете(л|ло)",
    ),
    (
        "ошибка",
        "шаблонный заголовок LLM",
        # «Ключевые точки» — термин компьютерного зрения (keypoints), а не шаблон:
        # он законно стоит заголовком в уроках про позу и лицо, поэтому исключён.
        r"<h[1-6][^>]*>\s*(Ключевые(?!\s+точки)|Итоги|Выводы|Частые (ошибки|проблемы)"
        r"|Связанные понятия|Дальше|Что дальше|Чек-лист|Заключение)",
    ),
    (
        "ошибка",
        "анонс будущего материала",
        r"в следующем (уроке|модуле|шаге)|дальше в курсе|дальше по курсу|мы ещё вернёмся|ещё вернёмся к этому|разбер[её]м (позже|дальше|в модуле)",
    ),
    (
        "ошибка",
        "ссылка на урок или модуль по номеру",
        r"в модуле \d|в уроке \d|урока \d\.\d|уроке \d\.\d",
    ),
    (
        "ошибка",
        "слово «зрелый» без критерия",
        r"зрел(ый|ая|ое|ые|ее|ость|остью)",
    ),
    (
        "предупреждение",
        "очеловечивание — проверить подлежащее",
        # Ловится и законное «студент понимает», поэтому не ошибка: смотреть, кто действует.
        r"понима(ет|ют)|зна(ет|ют), (что|какой|где)|реша(ет|ют) сам|догадыва\w+",
    ),
    (
        "предупреждение",
        "вода и мета-комментарии",
        # Ловим глагольные формы «подчёркивает / подчёркивая»: существительное
        # «подчёркивание» — это символ _ в имени файла, а не мета-комментарий.
        r"важно (понимать|отметить|помнить)|стоит отметить|следует отметить|таким образом"
        r"|в заключение|как мы уже выяснили|подчёркива(ет|ют|ем|л|ла|ли|я)\b|подчеркн(ём|ем|ут)\b",
    ),
    (
        "предупреждение",
        "англицизм при живом русском эквиваленте",
        r"\bscope\b|\bworkflow\b|\boutput\b|воркфлоу|пайплайн|юзер\w*|таск[аи]\b|фич[аи]\b",
    ),
    (
        "предупреждение",
        "мета-абзац о том, чему учит урок",
        r"в этом (уроке|шаге|задании) (вы|мы) (научит|разбер|узна|тренир)",
    ),
]


# Ширина колонки шага на latorn.ru, снятая по отрисованным шагам курса
# computer-vision: строки в 91 и 92 символа (XML-выгрузка в 03/02/05) и в 93
# (08/03/03) видны целиком, прокрутки у блока нет; 100 упирается в правый край;
# 104 и 105 уже обрезаны. Отсюда жёсткий предел 100, а от 95 — предупреждение:
# такая строка ещё влезает, но запаса на масштаб шрифта у студента не остаётся.
CODE_LINE_LIMIT = 95
CODE_LINE_HARD = 100


# Языки, у которых длинную строку не разорвать без вреда: вывод прогона рвать
# нельзя (это снимок, а не наш текст), а команда оболочки переносится в cmd,
# PowerShell и bash по-разному, и многострочный вариант ломает копирование.
UNBREAKABLE = {"no-highlight", "bash", "shell", "sh", "powershell", "console", "text"}


def code_lines(path: Path, text: str) -> list[tuple[int, str, str]]:
    """Строки блоков кода: (номер строки в файле, содержимое без разметки, язык).

    В HTML блок кода — это `<pre>`; теги `<code>` и сущности (`&lt;`, `&amp;`)
    из подсчёта убираются, иначе длина считается по разметке, а не по тому,
    что видит студент. В Markdown блок кода огорожен ```.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    result: list[tuple[int, str, str]] = []
    lang = ""

    if path.suffix == ".html":
        inside = False
        for number, line in enumerate(lines, 1):
            opened = re.search(r"<pre\b", line, re.I)
            closed = re.search(r"</pre>", line, re.I)
            if opened or inside:
                content = line
                if opened:
                    content = content[opened.end() :]
                    content = re.sub(r"^[^>]*>", "", content)
                    marker = re.search(r'class="language-([\w-]+)"', line, re.I)
                    lang = marker.group(1) if marker else ""
                if closed:
                    content = content[: content.lower().rfind("</pre>")]
                content = re.sub(r"</?code\b[^>]*>", "", content, flags=re.I)
                result.append((number, html.unescape(content), lang))
            inside = bool(opened or inside) and not closed
        return result

    inside = False
    for number, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            if not inside:
                lang = line.strip().lstrip("`").strip()
            inside = not inside
            continue
        if inside:
            result.append((number, line, lang))
    return result


def check_code_width(path: Path, text: str) -> list[tuple[str, str]]:
    """Блок кода не должен уезжать за правый край шага."""
    problems: list[tuple[str, str]] = []
    for number, line, lang in code_lines(path, text):
        if len(line) <= CODE_LINE_LIMIT:
            continue
        if len(line) > CODE_LINE_HARD and lang not in UNBREAKABLE:
            level, tail = "ошибка", "обрезана, блок уедет в прокрутку"
        else:
            level, tail = "предупреждение", "впритык к правому краю или за ним"
        problems.append(
            (level, f"строка {number}: {len(line)} символов — {tail}: {line.strip()[:60]}…")
        )
    return problems


def find_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(p for p in target.rglob("task.*") if p.suffix in {".html", ".md"})


def check_canon(path: Path, raw: bytes) -> list[str]:
    """Канон Latorn: CRLF, без хвостового перевода строки, картинка не в абзаце."""
    problems: list[str] = []
    if raw.endswith(b"\n"):
        problems.append("файл заканчивается переводом строки — канон это запрещает")
    lone_lf = raw.count(b"\n") - raw.count(b"\r\n")
    if lone_lf:
        problems.append(f"переводы строк не платформенные: {lone_lf} одиночных LF вместо CRLF")

    text = raw.decode("utf-8")
    if re.search(r"<p>\s*<img", text):
        problems.append("<img> обёрнут в <p> — даёт лишний перенос при отрисовке")
    if path.suffix == ".html" and "`" in text:
        problems.append("сырая обратная кавычка в HTML — заменяется на &#96;")
    # Граница после имени тега обязательна, иначе `p` совпадает с началом `pre`
    # и блок кода склеивается со следующим абзацем.
    for block in re.finditer(r"<(p|td|li|blockquote)(?=[\s>])[^>]*>(.*?)</\1>", text, re.S):
        if block.group(2).count("$") > 1:
            problems.append("два знака доллара в одном элементе — платформа съест их как формулу")
            break
    return problems


def mask_code(text: str) -> str:
    """Затереть блоки кода пробелами: правила стиля относятся к прозе, не к коду.

    Иначе имена переменных Python попадают под запреты — `output` и `keep`
    ловятся как англицизмы. Длина строки сохраняется, поэтому позиции совпадений
    остаются верными и контекст печатается по исходному тексту.
    """
    return re.sub(
        r"<pre\b.*?</pre>", lambda m: " " * len(m.group(0)), text, flags=re.S | re.I
    )


def context(text: str, start: int, end: int, width: int = 55) -> str:
    left = max(0, start - width)
    right = min(len(text), end + width)
    return text[left:right].replace("\r\n", " ").replace("\n", " ").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="папка урока или отдельный файл шага")
    parser.add_argument("--quiet", action="store_true", help="печатать только нарушения")
    args = parser.parse_args()

    if not args.target.exists():
        raise SystemExit(f"нет пути {args.target}")

    files = find_files(args.target)
    if not files:
        raise SystemExit(f"в {args.target} не нашлось файлов task.html или task.md")

    errors = 0
    warnings = 0

    for path in files:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        found: list[tuple[str, str, str]] = []

        for problem in check_canon(path, raw):
            found.append(("ошибка", "канон файла", problem))
        for level, problem in check_code_width(path, text):
            found.append((level, "длинная строка кода", problem))

        prose = mask_code(text)
        for level, name, pattern in RULES:
            for match in re.finditer(pattern, prose, re.I):
                found.append((level, name, "…" + context(text, match.start(), match.end()) + "…"))

        if not found and args.quiet:
            continue

        print(f"\n=== {path}")
        if not found:
            print("    чисто")
            continue
        for level, name, detail in found:
            mark = "✗" if level == "ошибка" else "•"
            print(f"    {mark} {name}: {detail}")
            if level == "ошибка":
                errors += 1
            else:
                warnings += 1

    print(f"\nитог: ошибок {errors}, предупреждений {warnings}, файлов {len(files)}")
    if warnings:
        print("предупреждения — не запрет, а место, куда надо посмотреть глазами")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
