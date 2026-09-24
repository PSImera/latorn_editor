import json
from pathlib import Path

from src.config import EditorConfig
from src.latorn_downloader import CourseDownloader

if __name__ == "__main__":
    defaults = json.loads(Path("course_config.json").read_text(encoding="utf-8"))
    all_courses: dict[str, str] = defaults["courses"]
    active: list[str] = defaults.get("upload", [])
    courses = {name: all_courses[name] for name in active if name in all_courses}

    for course_name, course_id in courses.items():
        print(f"\n=== курс {course_name} ({course_id}) ===")
        config = EditorConfig(course_id=course_id, course_name=course_name)
        uploader = CourseDownloader(config)

        # uploader.upload_structure(sync_deletions=True)

        uploader.upload_course(
            module_num=10,
            lesson_num=3,
            # step_num=4,
            # dry_run=True,
            # sync_structure=True
        )
