from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class EditorConfig:
    # Downloader: course_id — id (cuid) или slug курса на Latorn
    course_id: str = ""
    course_name: str = ""
    course_save_folder: str = "courses"
    encoding: str = "utf-8"

    # Course RAG
    course_rag_root: str = "course_rag"
    rag_embedding_model: str = "intfloat/multilingual-e5-large"
    rag_embedding_offline: bool = True
    rag_chunk_size: int = 2000
    rag_chunk_overlap: int = 200
    rag_indexed_step_types: list[str] = field(default_factory=lambda: ["text"])
