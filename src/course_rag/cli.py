from __future__ import annotations

import argparse
import json
import sys

from src.course_rag.indexer import CourseRAGIndex


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Course-specific vector RAG for Stepik lessons")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build a course index from HTML lessons")
    build_parser.add_argument("--course", required=True)
    build_parser.add_argument("--module-num", type=int)
    build_parser.add_argument("--lesson-num", type=int)
    build_parser.add_argument("--step-num", type=int)

    update_parser = subparsers.add_parser("update", help="Update one lesson step in a course index")
    update_parser.add_argument("--course", required=True)
    update_parser.add_argument("--file", required=True)

    migrate_parser = subparsers.add_parser(
        "migrate-legacy",
        help="Migrate legacy langchain/HNSW index to flat FAISS without re-embedding",
    )
    migrate_parser.add_argument("--course", required=True)

    search_parser = subparsers.add_parser("search", help="Search inside a course index")
    search_parser.add_argument("--course", required=True)
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--k", type=int, default=5)
    search_parser.add_argument("--module", type=int)
    search_parser.add_argument("--lesson", type=int)
    search_parser.add_argument("--step", type=int)

    history_parser = subparsers.add_parser(
        "search-history",
        help="Search only within the already covered part of a course up to a lesson boundary",
    )
    history_parser.add_argument("--course", required=True)
    history_parser.add_argument("--query", required=True)
    history_parser.add_argument("--k", type=int, default=5)
    history_parser.add_argument("--module-to", type=int, required=True)
    history_parser.add_argument("--lesson-to", type=int, required=True)
    history_parser.add_argument("--step-to", type=int)

    return parser


def main() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = build_parser()
    args = parser.parse_args()

    rag = CourseRAGIndex(course=args.course)

    if args.command == "build":
        print(
            json.dumps(
                rag.sync(
                    module_num=args.module_num,
                    lesson_num=args.lesson_num,
                    step_num=args.step_num,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "update":
        print(json.dumps(rag.update_step(args.file), ensure_ascii=False, indent=2))
        return

    if args.command == "migrate-legacy":
        print(json.dumps(rag.migrate_legacy_index_without_reembedding(), ensure_ascii=False, indent=2))
        return

    if args.command == "search":
        result = rag.search(
            query=args.query,
            k=args.k,
            module=args.module,
            lesson=args.lesson,
            step=args.step,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "search-history":
        result = rag.search_history(
            query=args.query,
            k=args.k,
            module_to=args.module_to,
            lesson_to=args.lesson_to,
            step_to=args.step_to,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return


if __name__ == "__main__":
    main()
