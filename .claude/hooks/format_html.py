import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.latorn_downloader.html_format import format_step_html

path = sys.argv[1]
with open(path, "rb") as f:
    raw = f.read()
# Зеркалит _write_task_text в даунлодере: контент шага (task.html / task.md)
# хранится как content.strip() (без BOM, обрамляющих пробелов и финального
# перевода строки); переводы строк — платформенные (os.linesep, на Windows CRLF),
# как их пишет Path.write_text без параметра newline. HTML дополнительно
# раскладывается по строкам для читаемости, markdown пишется как есть.
text = raw.decode("utf-8-sig").replace("\r\n", "\n").strip()
if Path(path).name == "task.html":
    text = format_step_html(text)
canonical = text.replace("\n", os.linesep).encode("utf-8")
if canonical != raw:
    with open(path, "wb") as f:
        f.write(canonical)
