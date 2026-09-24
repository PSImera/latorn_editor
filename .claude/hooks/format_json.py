import json
import os
import sys

path = sys.argv[1]
# structure.json пишется даунлодером с indent=2 (course_downloader.py), остальные JSON — indent=4
indent = 2 if os.path.basename(path) == "structure.json" else 4
with open(path, "r", encoding="utf-8-sig") as f:
    data = json.load(f)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=indent)
