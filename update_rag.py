import json
from pathlib import Path

from src.course_rag.indexer import CourseRAGIndex

if __name__ == "__main__":
    defaults = json.loads(Path("course_config.json").read_text(encoding="utf-8"))
    course = defaults["default_course"]

    rag = CourseRAGIndex(course=course)
    result = rag.sync(
        module_num=3,
        lesson_num=3,
        # step_num=1,
    )
    print(result)