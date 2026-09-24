import argparse
import json
import re
import sys
from html import unescape
from pathlib import Path
from typing import Any


# Поля описания курса Latorn (camelCase — как в API и structure.json)
COURSE_INFO_FIELDS = (
    "description",
    "summary",
    "targetAudience",
    "learningOutcomes",
    "formatDetails",
    "prerequisites",
)

HTML_FIELD_NAMES = {"summary"}

LIST_FIELD_NAMES = {"targetAudience", "learningOutcomes", "formatDetails", "prerequisites"}


def _normalize_text(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _html_to_text(value: str) -> str:
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"(^|\n)-\s*\n", r"\1- ", text)
    return _normalize_text(text)


def _clean_value(field_name: str, value: Any) -> Any:
    if isinstance(value, str):
        if field_name in HTML_FIELD_NAMES:
            return _html_to_text(value)
        return _normalize_text(unescape(value))

    if isinstance(value, list):
        cleaned_items = []
        for item in value:
            if isinstance(item, str):
                cleaned = _normalize_text(unescape(item))
                if cleaned:
                    cleaned_items.append(cleaned)
            else:
                cleaned_items.append(item)
        return cleaned_items

    return value


def extract_course_structure(data: dict[str, Any]) -> dict[str, Any]:
    course_info = data.get("course_info", {})
    extracted_info = {
        field: _clean_value(
            field,
            course_info.get(field, [] if field in LIST_FIELD_NAMES else ""),
        )
        for field in COURSE_INFO_FIELDS
    }

    structure = []
    for section in data.get("structure", []):
        structure.append(
            {
                "section_title": _clean_value(
                    "section_title", section.get("section_title", "")
                ),
                "section_position": section.get("section_position"),
                "lessons": [
                    {
                        "lesson_title": _clean_value(
                            "lesson_title", lesson.get("lesson_title", "")
                        ),
                        "position": lesson.get("position"),
                    }
                    for lesson in section.get("lessons", [])
                ],
            }
        )

    return {
        "course_name": _clean_value("course_name", data.get("course_name", "")),
        "course_info": extracted_info,
        "structure": structure,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract only the learner-facing course metadata and module/lesson titles "
            "from a course structure.json file (courses/<course>/structure.json)."
        )
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the course structure.json file.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the extracted JSON. Defaults to stdout.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    raw_data = json.loads(input_path.read_text(encoding="utf-8"))
    extracted = extract_course_structure(raw_data)
    rendered = json.dumps(extracted, ensure_ascii=False, indent=2)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    else:
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
        print(rendered)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
