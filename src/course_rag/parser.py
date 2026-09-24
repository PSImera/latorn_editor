from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup, Tag
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config import EditorConfig

HEADING_MARKERS = {"h1": "##H1##", "h2": "##H2##", "h3": "##H3##"}
BLOCK_TAGS = ("p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "li")
NOISE_HEADING_PREFIX = re.compile(r"^[^\wА-Яа-яA-Za-z0-9]+", re.UNICODE)
WHITESPACE_RE = re.compile(r"[ \t\u00a0]+")


@dataclass(frozen=True)
class StepAddress:
    course: str
    module: str
    lesson: str
    step: str
    step_type: str
    file_path: Path

    @property
    def module_num(self) -> int:
        return int(self.module)

    @property
    def lesson_num(self) -> int:
        return int(self.lesson)

    @property
    def step_num(self) -> int:
        return int(self.step)


def parse_step_address(file_path: Path, courses_root: Path) -> StepAddress:
    file_path = file_path.resolve()
    courses_root = courses_root.resolve()
    rel = file_path.relative_to(courses_root)
    parts = rel.parts
    if len(parts) != 5:
        raise ValueError(f"Unexpected lesson path (expected 5 parts): {file_path}")

    course, module, lesson, step_folder, _ = parts
    folder_match = re.fullmatch(r"(\d{2})_(.+)", step_folder)
    if not folder_match:
        raise ValueError(f"Unexpected step folder format: {step_folder}")

    step = folder_match.group(1)
    step_type = folder_match.group(2)

    return StepAddress(
        course=course,
        module=module,
        lesson=lesson,
        step=step,
        step_type=step_type,
        file_path=file_path,
    )


def clean_heading(text: str) -> str:
    text = text.replace("\ufeff", " ").replace("\u200b", " ")
    text = WHITESPACE_RE.sub(" ", text.strip())
    text = NOISE_HEADING_PREFIX.sub("", text)
    return text.strip()


def normalize_text(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()




def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _replace_code_blocks(node: Tag | BeautifulSoup) -> None:
    for pre in node.find_all("pre"):
        code = pre.find("code")
        code_text = code.get_text("\n", strip=False) if code else pre.get_text("\n", strip=False)
        pre.replace_with(f"\n```\n{code_text.strip()}\n```\n")

    for code in node.find_all("code"):
        if code.find_parent("pre"):
            continue
        code.replace_with(f"`{code.get_text(strip=True)}`")


def _replace_lists(node: Tag | BeautifulSoup) -> None:
    for ul in node.find_all("ul"):
        lines = []
        for li in ul.find_all("li", recursive=False):
            text = normalize_text(li.get_text("\n", strip=True))
            if text:
                lines.append(f"- {text}")
        ul.replace_with("\n" + "\n".join(lines) + "\n")

    for ol in node.find_all("ol"):
        lines = []
        for index, li in enumerate(ol.find_all("li", recursive=False), start=1):
            text = normalize_text(li.get_text("\n", strip=True))
            if text:
                lines.append(f"{index}. {text}")
        ol.replace_with("\n" + "\n".join(lines) + "\n")


def _replace_tables(node: Tag | BeautifulSoup) -> None:
    for table in node.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [normalize_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(" | ".join(cells))
        table.replace_with("\n" + "\n".join(rows) + "\n")


def _markdown_to_marked_text(raw: str) -> str:
    """Переводит markdown-шаг в тот же промежуточный формат, что и HTML-ветка:
    заголовки h1–h3 → маркеры ##H1##..##H3##, код в fences остаётся как есть."""
    lines: list[str] = []
    in_fence = False
    for line in raw.replace("\r", "").splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            lines.append(line)
            continue
        if in_fence:
            lines.append(line)
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            marker = HEADING_MARKERS[f"h{len(heading.group(1))}"]
            title = clean_heading(heading.group(2))
            lines.append(f"\n\n{marker} {title}\n\n" if title else "")
            continue
        lines.append(line)
    return normalize_text("\n".join(lines))


def _html_to_marked_text(raw: str) -> str:
    soup = BeautifulSoup(raw, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    root = soup.find("main") or soup.find("body") or soup

    for br in root.find_all("br"):
        br.replace_with("\n")

    _replace_code_blocks(root)
    _replace_lists(root)
    _replace_tables(root)

    for heading_name, marker in HEADING_MARKERS.items():
        for heading in root.find_all(heading_name):
            title = clean_heading(heading.get_text(" ", strip=True))
            if title:
                heading.replace_with(f"\n\n{marker} {title}\n\n")
            else:
                heading.decompose()

    for block in root.find_all(BLOCK_TAGS):
        if getattr(block, "string", None) is not None:
            block.string = f"{block.string}\n\n"
        else:
            block.append("\n\n")

    return normalize_text(root.get_text("\n", strip=False))


def extract_step_text(file_path: Path) -> tuple[str, str]:
    raw = file_path.read_text(encoding="utf-8")
    if file_path.suffix == ".md":
        text = _markdown_to_marked_text(raw)
    else:
        text = _html_to_marked_text(raw)
    step_title = ""
    for line in text.splitlines():
        if line.startswith("##H1## "):
            step_title = clean_heading(line.replace("##H1## ", "", 1))
            break
        if line.startswith("##H2## "):
            step_title = clean_heading(line.replace("##H2## ", "", 1))
            break

    return text, step_title


def split_step_into_documents(
    file_path: Path,
    config: EditorConfig,
    courses_root: Path,
) -> list[Document]:
    address = parse_step_address(file_path, courses_root)
    text, fallback_title = extract_step_text(file_path)

    splitter = RecursiveCharacterTextSplitter(
        separators=["\n\n##H2## ", "\n\n##H3## ", "\n\n", "\n", ". ", " "],
        chunk_size=config.rag_chunk_size,
        chunk_overlap=config.rag_chunk_overlap,
    )

    section_title = ""
    subsection_title = ""
    current_lines: list[str] = []
    raw_chunks: list[tuple[str, str, str]] = []

    def flush() -> None:
        chunk_text = normalize_text("\n".join(current_lines))
        if chunk_text:
            raw_chunks.append((chunk_text, section_title, subsection_title))
        current_lines.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("##H1## "):
            continue
        if stripped.startswith("##H2## "):
            flush()
            section_title = clean_heading(stripped.replace("##H2## ", "", 1))
            subsection_title = ""
            continue
        if stripped.startswith("##H3## "):
            flush()
            subsection_title = clean_heading(stripped.replace("##H3## ", "", 1))
            continue
        current_lines.append(stripped)
    flush()

    if not raw_chunks:
        raw_chunks = [(text, "", "")]

    mtime = file_path.stat().st_mtime
    step_title = fallback_title or f"Шаг {address.step_num}"
    step_type = address.step_type
    docs: list[Document] = []
    chunk_seq = 1

    for raw_text, top_section, sub_section in raw_chunks:
        split_chunks = splitter.split_text(raw_text) if len(raw_text) > config.rag_chunk_size else [raw_text]
        for chunk_text in split_chunks:
            clean_chunk = normalize_text(chunk_text)
            if not clean_chunk:
                continue
            docs.append(
                Document(
                    page_content=clean_chunk,
                    metadata={
                        "course": address.course,
                        "module": address.module,
                        "lesson": address.lesson,
                        "step": address.step,
                        "module_num": address.module_num,
                        "lesson_num": address.lesson_num,
                        "step_num": address.step_num,
                        "step_type": step_type,
                        "step_title": step_title,
                        "section_title": top_section,
                        "subsection_title": sub_section,
                        "file_path": str(file_path),
                        "chunk_id": f"{address.module}-{address.lesson}-{address.step}:{chunk_seq}",
                        "source_mtime": mtime,
                        "content_hash": content_hash(clean_chunk),
                    },
                )
            )
            chunk_seq += 1

    return docs


def documents_to_jsonl_lines(documents: Iterable[Document]) -> list[str]:
    return [
        json.dumps({"page_content": doc.page_content, "metadata": doc.metadata}, ensure_ascii=False)
        for doc in documents
    ]


def documents_from_jsonl(path: Path) -> list[Document]:
    if not path.exists():
        return []

    docs: list[Document] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            docs.append(Document(page_content=payload["page_content"], metadata=payload["metadata"]))
    return docs
