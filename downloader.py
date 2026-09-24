import json
from pathlib import Path

from src.config import EditorConfig
from src.latorn_downloader import CourseDownloader

if __name__ == "__main__":
    defaults = json.loads(Path("course_config.json").read_text(encoding="utf-8"))
    all_courses: dict[str, str] = defaults["courses"]
    active: list[str] = defaults.get("download", [])
    courses = {name: all_courses[name] for name in active if name in all_courses}

    for course_name, course_id in courses.items():
        print(f"\n=== курс {course_name} ({course_id}) ===")
        config = EditorConfig(course_id=course_id, course_name=course_name)
        downloader = CourseDownloader(config)

        downloader.download_course(
            module_num=8,
            lesson_num=3,
            # step_num=4,
            # refresh_structure=True,
            # описание курса правили в веб-редакторе на сайте — забрать его
            # поверх локального (иначе uploader затрёт правки локальным текстом)
            # pull_course_info=True,
        )
