from __future__ import annotations

import hashlib
import json
import os
import re
import pickle
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import faiss
import numpy as np
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from sentence_transformers import SentenceTransformer
from tqdm.auto import tqdm
from transformers.utils import logging

from src.config import EditorConfig
from src.course_rag.parser import documents_from_jsonl, documents_to_jsonl_lines, split_step_into_documents

logging.set_verbosity_error()


class STEmbeddings(Embeddings):
    def __init__(self, model_name: str, *, offline: bool = False):
        resolved_model_name, resolved_from_cache = self._resolve_model_name(model_name)
        if offline and not resolved_from_cache and resolved_model_name == model_name:
            raise RuntimeError(
                "Embedding model is not available in the local cache while Course RAG is running in offline mode. "
                f"Requested model: '{model_name}'. "
                "To download it once, temporarily set EditorConfig.rag_embedding_offline=False and run the command again. "
                "After the model is cached locally, switch offline mode back on."
            )
        try:
            if offline:
                os.environ.setdefault("HF_HUB_OFFLINE", "1")
                os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            self.model = SentenceTransformer(
                resolved_model_name,
                trust_remote_code=True,
                local_files_only=offline,
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to load the embedding model. "
                f"Requested model: '{model_name}'. "
                "If you changed the model and it is not cached yet, temporarily set "
                "EditorConfig.rag_embedding_offline=False to allow one-time download, "
                "or point EditorConfig.rag_embedding_model to a local cached model path."
            ) from exc

    @staticmethod
    def _resolve_model_name(model_name: str) -> tuple[str, bool]:
        candidate = Path(model_name)
        if candidate.exists():
            return str(candidate), True

        if "/" not in model_name:
            return model_name, False

        org, repo = model_name.split("/", 1)
        for hub_root in STEmbeddings._hub_roots():
            snapshot_root = hub_root / f"models--{org}--{repo}" / "snapshots"
            if not snapshot_root.exists():
                continue

            snapshots = [path for path in snapshot_root.iterdir() if path.is_dir()]
            if not snapshots:
                continue

            snapshots.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            return str(snapshots[0]), True

        return model_name, False

    @staticmethod
    def _hub_roots() -> list[Path]:
        """Кэш HuggingFace: HF_HUB_CACHE, затем HF_HOME/hub, затем дефолт ~/.cache."""
        roots: list[Path] = []
        hub_cache = os.environ.get("HF_HUB_CACHE")
        if hub_cache:
            roots.append(Path(hub_cache))
        hf_home = os.environ.get("HF_HOME")
        if hf_home:
            roots.append(Path(hf_home) / "hub")
        roots.append(Path.home() / ".cache" / "huggingface" / "hub")
        return roots

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self.model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return cast(list[list[float]], vectors.astype(np.float32).tolist())

    def embed_query(self, text: str) -> list[float]:
        vector = self.model.encode(
            text,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return cast(list[float], vector.astype(np.float32).tolist())


class CourseRAGIndex:
    INDEX_BATCH_SIZE = 64

    def __init__(self, course: str, config: EditorConfig | None = None):
        self.config = config or EditorConfig()
        self.course = course
        self.courses_root = Path(self.config.course_save_folder).resolve()
        self.course_root = self.courses_root / course
        self.storage_root = Path(self.config.course_rag_root).resolve() / course
        self.storage_root.mkdir(parents=True, exist_ok=True)

        self.index_file = self.storage_root / "index.faiss"
        self.store_file = self.storage_root / "index.pkl"
        self.metadata_file = self.storage_root / "metadata.json"
        self.chunks_file = self.storage_root / "chunks.jsonl"

        self._log(f"Инициализация Course RAG для курса '{self.course}'")
        self._log(f"Папка курса: {self.course_root}")
        self._log(f"Папка индекса: {self.storage_root}")
        self.embeddings: Embeddings | None = None
        self.embedding_dim = self._detect_embedding_dim()
        self._log(f"Embeddings model: {self.config.rag_embedding_model}, размерность: {self.embedding_dim}")
        self.vector_index: faiss.IndexIDMap2 | None = None
        self.docstore: dict[int, Document] = {}

    def sync(
        self,
        module_num: int | None = None,
        lesson_num: int | None = None,
        step_num: int | None = None,
    ) -> dict[str, Any]:
        sync_started_at = time.perf_counter()
        scope = {
            "module_num": module_num,
            "lesson_num": lesson_num,
            "step_num": step_num,
        }

        if lesson_num is not None and module_num is None:
            raise ValueError("lesson_num requires module_num")
        if step_num is not None and (module_num is None or lesson_num is None):
            raise ValueError("step_num requires both module_num and lesson_num")

        self._log("=" * 60)
        self._log(f"Синхронизация индекса курса '{self.course}'")
        self._log(f"Область: {self._scope_label(module_num, lesson_num, step_num)}")

        if module_num is None:
            self._log("Режим: полная пересборка курса")
            documents = self._scan_course_documents()
            operation = "build"
            self._log(f"Всего чанков для сохранения: {len(documents)}")
            self._log("Сохранение обновленного chunks.jsonl...")
            self._persist_documents(documents)
            if not documents:
                self._log("Подходящих файлов шагов не найдено, очищаю файлы индекса")
                self._clear_vector_store_files()
                metadata = self._save_metadata(documents, operation, scope=scope)
                self._print_statistics(metadata)
                self._log(f"Итоговое время синхронизации: {self._format_elapsed(sync_started_at)}")
                return metadata

            self._log(f"Полная пересборка flat-индекса для всего курса ({len(documents)} чанков)...")
            self._build_vector_store(documents, operation=operation)
        else:
            self._log("Режим: частичная синхронизация области")
            documents, new_docs = self._merge_scope_documents(
                module_num=module_num,
                lesson_num=lesson_num,
                step_num=step_num,
            )
            operation = "update_scope"
            self._log(f"Всего чанков для сохранения: {len(documents)}")
            self._log("Сохранение обновленного chunks.jsonl...")
            self._persist_documents(documents)
            if not documents:
                self._log("Подходящих файлов шагов не найдено, очищаю файлы индекса")
                self._clear_vector_store_files()
                metadata = self._save_metadata(documents, operation, scope=scope)
                self._print_statistics(metadata)
                self._log(f"Итоговое время синхронизации: {self._format_elapsed(sync_started_at)}")
                return metadata

            self._log(
                "Частичная синхронизация области завершена: обновляю flat-индекс только для измененной "
                f"области ({len(new_docs)} новых чанков), остальные векторы переиспользуются"
            )
            self._update_vector_store_scope(
                documents=documents,
                new_docs=new_docs,
                module_num=module_num,
                lesson_num=lesson_num,
                step_num=step_num,
            )

        metadata = self._save_metadata(documents, operation, scope=scope)
        self._print_statistics(metadata)
        self._log(f"Итоговое время синхронизации: {self._format_elapsed(sync_started_at)}")
        return metadata

    def update_step(self, html_path: str | Path) -> dict[str, Any]:
        update_started_at = time.perf_counter()
        path = Path(html_path).resolve()
        if not path.exists():
            raise FileNotFoundError(path)

        self._log("=" * 60)
        self._log(f"Точечное обновление шага: {path}")
        existing_docs = documents_from_jsonl(self.chunks_file)
        self._log(f"Текущих чанков в chunks.jsonl: {len(existing_docs)}")
        retained_docs = [doc for doc in existing_docs if Path(doc.metadata["file_path"]).resolve() != path]
        new_docs = split_step_into_documents(path, self.config, self.courses_root)
        self._log(f"Новых чанков для шага: {len(new_docs)}")
        documents = retained_docs + new_docs

        if not documents:
            raise ValueError(f"No RAG chunks found for {path}")

        self._log("Сохранение chunks.jsonl...")
        self._persist_documents(documents)
        metadata = new_docs[0].metadata if new_docs else self._parse_step_path(path)
        self._log(
            "Точечное обновление шага завершено: обновляю flat-индекс только для одного шага, "
            "остальные векторы переиспользуются"
        )
        self._update_vector_store_scope(
            documents=documents,
            new_docs=new_docs,
            module_num=int(metadata["module_num"]),
            lesson_num=int(metadata["lesson_num"]),
            step_num=int(metadata["step_num"]),
        )
        saved_metadata = self._save_metadata(documents, "update", updated_file=str(path))
        self._print_statistics(saved_metadata)
        self._log(f"Итоговое время обновления шага: {self._format_elapsed(update_started_at)}")
        return saved_metadata

    def search(
        self,
        query: str,
        k: int = 5,
        module: int | None = None,
        lesson: int | None = None,
        step: int | None = None,
    ) -> list[dict[str, Any]]:
        self._ensure_loaded()
        if self.vector_index is None:
            return []

        total = self.vector_index.ntotal
        if total == 0:
            return []

        query_vector = np.asarray([self._get_embeddings().embed_query(query)], dtype=np.float32)
        fetch_k = min(max(k * 5, 20), total)
        scores, ids = self.vector_index.search(query_vector, fetch_k)  # type: ignore[call-arg]

        results: list[dict[str, Any]] = []
        for score, doc_id in zip(scores[0], ids[0]):
            if doc_id == -1:
                continue
            doc = self.docstore.get(int(doc_id))
            if doc is None:
                continue

            metadata = doc.metadata
            if module is not None and int(metadata["module_num"]) != module:
                continue
            if lesson is not None and int(metadata["lesson_num"]) != lesson:
                continue
            if step is not None and int(metadata["step_num"]) != step:
                continue

            results.append(
                {
                    "score": float(score),
                    "page_content": doc.page_content,
                    "metadata": metadata,
                }
            )
            if len(results) >= k:
                break

        return results

    def search_history(
        self,
        query: str,
        *,
        module_to: int,
        lesson_to: int,
        step_to: int | None = None,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        self._ensure_loaded()
        if self.vector_index is None:
            return []

        total = self.vector_index.ntotal
        if total == 0:
            return []

        query_vector = np.asarray([self._get_embeddings().embed_query(query)], dtype=np.float32)
        fetch_k = min(max(k * 12, 50), total)
        scores, ids = self.vector_index.search(query_vector, fetch_k)  # type: ignore[call-arg]

        results: list[dict[str, Any]] = []
        for score, doc_id in zip(scores[0], ids[0]):
            if doc_id == -1:
                continue
            doc = self.docstore.get(int(doc_id))
            if doc is None:
                continue

            metadata = doc.metadata
            if not self._is_at_or_before_boundary(
                metadata=metadata,
                module_to=module_to,
                lesson_to=lesson_to,
                step_to=step_to,
            ):
                continue

            results.append(
                {
                    "score": float(score),
                    "page_content": doc.page_content,
                    "metadata": metadata,
                }
            )
            if len(results) >= k:
                break

        return results

    def migrate_legacy_index_without_reembedding(self) -> dict[str, Any]:
        migrate_started_at = time.perf_counter()
        self._log("=" * 60)
        self._log(f"Миграция legacy-индекса курса '{self.course}' без пересчета эмбеддингов")

        legacy_index = faiss.read_index(str(self.index_file))
        legacy_store, legacy_mapping = self._load_legacy_langchain_store()

        self._log(f"Legacy тип индекса: {type(legacy_index).__name__}")
        self._log(f"Legacy чанков в FAISS: {legacy_index.ntotal}")
        self._log(f"Legacy записей в mapping: {len(legacy_mapping)}")

        if legacy_index.ntotal != len(legacy_mapping):
            raise ValueError(
                "Legacy FAISS index and index_to_docstore_id have different sizes: "
                f"{legacy_index.ntotal} vs {len(legacy_mapping)}"
            )

        documents: list[Document] = []
        vectors: list[np.ndarray] = []
        restore_started_at = time.perf_counter()
        progress = tqdm(
            range(legacy_index.ntotal),
            total=legacy_index.ntotal,
            desc="Миграция legacy-чанков",
            unit="chunk",
        )
        for position in progress:
            docstore_key = legacy_mapping[position]
            doc = self._legacy_docstore_get(legacy_store, docstore_key)
            if doc is None:
                raise KeyError(f"Document not found in legacy docstore: {docstore_key}")
            vector = legacy_index.reconstruct(position)
            documents.append(doc)
            vectors.append(np.asarray(vector, dtype=np.float32))
            progress.set_postfix_str(f"{position + 1}/{legacy_index.ntotal} chunks")
        self._log(
            "Восстановление документов и векторов из legacy-индекса завершено "
            f"за {self._format_elapsed(restore_started_at)}"
        )

        self._backup_legacy_files()
        self._persist_documents(documents)

        self.vector_index = faiss.IndexIDMap2(faiss.IndexFlatIP(self.embedding_dim))
        self.docstore = {}
        add_started_at = time.perf_counter()
        ids = np.asarray([self._document_id(doc) for doc in documents], dtype=np.int64)
        self._validate_new_ids(ids)
        matrix = np.vstack(vectors).astype(np.float32)
        self.vector_index.add_with_ids(matrix, ids)  # type: ignore[call-arg]
        for doc_id, doc in zip(ids.tolist(), documents):
            self.docstore[int(doc_id)] = doc
        self._log(
            "Перенос в новый flat-индекс завершен "
            f"за {self._format_elapsed(add_started_at)}"
        )

        self._save_vector_store()
        metadata = self._save_metadata(documents, "migrate_legacy_without_reembedding")
        self._print_statistics(metadata)
        self._log(f"Итоговое время миграции: {self._format_elapsed(migrate_started_at)}")
        return metadata

    def has_index(self) -> bool:
        return self.index_file.exists() and self.store_file.exists() and self.chunks_file.exists()

    def _create_embeddings(self) -> Embeddings:
        self._log(f"Загрузка модели embeddings: {self.config.rag_embedding_model}")
        return STEmbeddings(
            self.config.rag_embedding_model,
            offline=self.config.rag_embedding_offline,
        )

    def _get_embeddings(self) -> Embeddings:
        if self.embeddings is None:
            self.embeddings = self._create_embeddings()
        return self.embeddings

    def _detect_embedding_dim(self) -> int:
        if self.metadata_file.exists():
            try:
                metadata = json.loads(self.metadata_file.read_text(encoding="utf-8"))
                dim = metadata.get("embedding_dim")
                if isinstance(dim, int) and dim > 0:
                    return dim
            except Exception:
                pass
        if self.index_file.exists():
            try:
                return int(faiss.read_index(str(self.index_file)).d)
            except Exception:
                pass
        embeddings = self._get_embeddings()
        return len(embeddings.embed_query("test query"))

    def _scan_course_documents(
        self,
        module_num: int | None = None,
        lesson_num: int | None = None,
        step_num: int | None = None,
    ) -> list[Document]:
        if not self.course_root.exists():
            raise FileNotFoundError(f"Course not found: {self.course_root}")

        indexed_types = set(self.config.rag_indexed_step_types)
        documents: list[Document] = []
        step_files = sorted(
            [*self.course_root.rglob("*/task.html"), *self.course_root.rglob("*/task.md")]
        )
        selected_files = [
            file_path
            for file_path in step_files
            if self._is_indexed_step(file_path, indexed_types)
            and self._matches_scope(
                file_path,
                module_num=module_num,
                lesson_num=lesson_num,
                step_num=step_num,
            )
        ]
        self._log(f"Файлов шагов найдено для обработки: {len(selected_files)}")
        scan_started_at = time.perf_counter()
        for index, file_path in enumerate(selected_files, start=1):
            self._log(f"[{index}/{len(selected_files)}] {file_path.relative_to(self.course_root)}")
            documents.extend(split_step_into_documents(file_path, self.config, self.courses_root))
        self._log(
            f"Разбор шагов в чанки завершен: {len(documents)} чанков за {self._format_elapsed(scan_started_at)}"
        )
        return documents

    def _merge_scope_documents(
        self,
        module_num: int,
        lesson_num: int | None = None,
        step_num: int | None = None,
    ) -> tuple[list[Document], list[Document]]:
        existing_docs = documents_from_jsonl(self.chunks_file)
        self._log(f"Текущих чанков в индексе: {len(existing_docs)}")
        retained_docs = [
            doc
            for doc in existing_docs
            if not self._matches_doc_scope(
                doc,
                module_num=module_num,
                lesson_num=lesson_num,
                step_num=step_num,
            )
        ]
        self._log(f"Чанков после удаления старой области: {len(retained_docs)}")
        new_docs = self._scan_course_documents(
            module_num=module_num,
            lesson_num=lesson_num,
            step_num=step_num,
        )
        self._log(f"Новых чанков для области: {len(new_docs)}")
        return retained_docs + new_docs, new_docs

    @staticmethod
    def _is_indexed_step(file_path: Path, indexed_types: set[str]) -> bool:
        m = re.fullmatch(r"\d{2}_(.+)", file_path.parent.name)
        return bool(m) and m.group(1) in indexed_types

    def _matches_scope(
        self,
        file_path: Path,
        module_num: int | None = None,
        lesson_num: int | None = None,
        step_num: int | None = None,
    ) -> bool:
        rel = file_path.relative_to(self.course_root)
        current_module = int(rel.parts[0])
        current_lesson = int(rel.parts[1])
        current_step = int(file_path.parent.name.split("_")[0])

        if module_num is not None and current_module != module_num:
            return False
        if lesson_num is not None and current_lesson != lesson_num:
            return False
        if step_num is not None and current_step != step_num:
            return False
        return True

    def _matches_doc_scope(
        self,
        doc: Document,
        module_num: int | None = None,
        lesson_num: int | None = None,
        step_num: int | None = None,
    ) -> bool:
        metadata = doc.metadata
        if module_num is not None and int(metadata["module_num"]) != module_num:
            return False
        if lesson_num is not None and int(metadata["lesson_num"]) != lesson_num:
            return False
        if step_num is not None and int(metadata["step_num"]) != step_num:
            return False
        return True

    @staticmethod
    def _is_at_or_before_boundary(
        *,
        metadata: dict[str, Any],
        module_to: int,
        lesson_to: int,
        step_to: int | None = None,
    ) -> bool:
        current = (
            int(metadata["module_num"]),
            int(metadata["lesson_num"]),
            int(metadata["step_num"]),
        )
        boundary = (
            int(module_to),
            int(lesson_to),
            int(step_to) if step_to is not None else 10**9,
        )
        return current <= boundary

    def _persist_documents(self, documents: list[Document]) -> None:
        with self.chunks_file.open("w", encoding="utf-8") as handle:
            for line in documents_to_jsonl_lines(documents):
                handle.write(line + "\n")
        self._log(f"chunks.jsonl сохранен: {self.chunks_file}")

    def _clear_vector_store_files(self) -> None:
        self.vector_index = None
        self.docstore = {}
        for path in (self.index_file, self.store_file):
            if path.exists():
                path.unlink()
                self._log(f"Удален файл индекса: {path}")

    def _build_vector_store(self, documents: list[Document], operation: str) -> None:
        build_started_at = time.perf_counter()
        if operation == "build":
            self._log("Создание нового flat-индекса FAISS (IndexFlatIP + IndexIDMap2)...")
        else:
            self._log("Создание flat-индекса FAISS (IndexFlatIP + IndexIDMap2)...")

        self.vector_index = faiss.IndexIDMap2(faiss.IndexFlatIP(self.embedding_dim))
        self.docstore = {}
        self._add_documents_with_progress(documents)
        self._save_vector_store()
        self._log(f"FAISS-индекс готов за {self._format_elapsed(build_started_at)}")

    def _update_vector_store_scope(
        self,
        documents: list[Document],
        new_docs: list[Document],
        module_num: int,
        lesson_num: int | None = None,
        step_num: int | None = None,
    ) -> None:
        update_started_at = time.perf_counter()
        if not self._try_load_existing_vector_store():
            self._log(
                "Готовый индекс не найден или несовместим с новым форматом, выполняю одноразовую "
                "полную сборку из текущего chunks.jsonl"
            )
            self._build_vector_store(documents, operation="rebuild")
            return

        ids_to_remove = [
            doc_id
            for doc_id, doc in self.docstore.items()
            if self._matches_doc_scope(
                doc,
                module_num=module_num,
                lesson_num=lesson_num,
                step_num=step_num,
            )
        ]
        self._log(f"Чанков для удаления из индекса: {len(ids_to_remove)}")
        if ids_to_remove:
            assert self.vector_index is not None
            remove_started_at = time.perf_counter()
            ids_array = np.asarray(ids_to_remove, dtype=np.int64)
            removed = self.vector_index.remove_ids(ids_array)
            for doc_id in ids_to_remove:
                self.docstore.pop(doc_id, None)
            self._log(
                f"Удалено из индекса: {removed} чанков за {self._format_elapsed(remove_started_at)}"
            )

        if new_docs:
            self._log(f"Добавляю в индекс только новые чанки области: {len(new_docs)}")
            self._add_documents_with_progress(new_docs)
        else:
            self._log("В обновляемой области новых чанков нет")

        self._save_vector_store()
        self._log(
            "Инкрементальное обновление индекса завершено "
            f"за {self._format_elapsed(update_started_at)}"
        )

    def _save_vector_store(self) -> None:
        if self.vector_index is None:
            raise RuntimeError("Vector index is not initialized")

        save_started_at = time.perf_counter()
        self._log("Сохранение flat-индекса на диск...")
        faiss.write_index(self.vector_index, str(self.index_file))
        with self.store_file.open("wb") as handle:
            pickle.dump(self._serialize_docstore(), handle)
        self._log(f"Индекс сохранен: {self.storage_root}")
        self._log(f"Сохранение индекса завершено за {self._format_elapsed(save_started_at)}")

    def _ensure_loaded(self) -> None:
        if self.vector_index is not None:
            return
        if not self.has_index():
            raise FileNotFoundError(f"Course RAG index is missing for course '{self.course}'")
        self._load_vector_store()

    def _try_load_existing_vector_store(self) -> bool:
        if self.vector_index is not None:
            return True
        if not self.index_file.exists() or not self.store_file.exists():
            return False
        try:
            self._load_vector_store()
        except Exception as exc:
            self._log(f"Не удалось загрузить существующий индекс: {exc}")
            self.vector_index = None
            self.docstore = {}
            return False
        return True

    def _load_vector_store(self) -> None:
        load_started_at = time.perf_counter()
        self._log("Загрузка существующего flat-индекса с диска...")
        index = faiss.read_index(str(self.index_file))
        if not isinstance(index, faiss.IndexIDMap2):
            raise TypeError("Unsupported FAISS index type. Expected IndexIDMap2.")

        with self.store_file.open("rb") as handle:
            payload = pickle.load(handle)

        if payload.get("embedding_dim") != self.embedding_dim:
            raise ValueError(
                f"Embedding dim mismatch: expected {self.embedding_dim}, got {payload.get('embedding_dim')}"
            )

        self.vector_index = index
        self.docstore = self._deserialize_docstore(payload)
        self._log(
            "Загрузка индекса завершена: "
            f"{self.vector_index.ntotal} чанков за {self._format_elapsed(load_started_at)}"
        )

    def _load_legacy_langchain_store(self) -> tuple[Any, dict[int, str]]:
        with self.store_file.open("rb") as handle:
            payload = pickle.load(handle)
        if not isinstance(payload, tuple) or len(payload) != 2:
            raise TypeError("Legacy store format is not a langchain tuple")
        legacy_store, legacy_mapping = payload
        if not isinstance(legacy_mapping, dict):
            raise TypeError("Legacy index_to_docstore_id must be a dict")
        normalized_mapping = {int(key): str(value) for key, value in legacy_mapping.items()}
        return legacy_store, normalized_mapping

    def _legacy_docstore_get(self, legacy_store: Any, key: str) -> Document | None:
        if hasattr(legacy_store, "search"):
            doc = legacy_store.search(key)
            if isinstance(doc, Document):
                return doc
        store_dict = getattr(legacy_store, "_dict", None)
        if isinstance(store_dict, dict):
            doc = store_dict.get(key)
            if isinstance(doc, Document):
                return doc
        return None

    def _backup_legacy_files(self) -> None:
        backup_pairs = [
            (self.index_file, self.storage_root / "index.legacy_hnsw.faiss.bak"),
            (self.store_file, self.storage_root / "index.legacy_langchain.pkl.bak"),
        ]
        for source, backup in backup_pairs:
            if source.exists() and not backup.exists():
                shutil.copy2(source, backup)
                self._log(f"Создана резервная копия: {backup}")

    def _serialize_docstore(self) -> dict[str, Any]:
        return {
            "format_version": 2,
            "index_type": "IndexFlatIP+IndexIDMap2",
            "embedding_dim": self.embedding_dim,
            "docstore": {
                str(doc_id): {
                    "page_content": doc.page_content,
                    "metadata": doc.metadata,
                }
                for doc_id, doc in self.docstore.items()
            },
        }

    def _deserialize_docstore(self, payload: dict[str, Any]) -> dict[int, Document]:
        raw_docstore = payload.get("docstore")
        if not isinstance(raw_docstore, dict):
            raise TypeError("Invalid docstore payload")

        docstore: dict[int, Document] = {}
        for raw_id, item in raw_docstore.items():
            doc_id = int(raw_id)
            docstore[doc_id] = Document(
                page_content=item["page_content"],
                metadata=item["metadata"],
            )
        return docstore

    def _save_metadata(
        self,
        documents: list[Document],
        operation: str,
        updated_file: str | None = None,
        scope: dict[str, int | None] | None = None,
    ) -> dict[str, Any]:
        unique_files = sorted({doc.metadata["file_path"] for doc in documents})
        metadata = {
            "course": self.course,
            "operation": operation,
            "updated_at": datetime.now().isoformat(),
            "embedding_model": self.config.rag_embedding_model,
            "embedding_dim": self.embedding_dim,
            "chunk_size": self.config.rag_chunk_size,
            "chunk_overlap": self.config.rag_chunk_overlap,
            "index_type": "IndexFlatIP+IndexIDMap2",
            "files_count": len(unique_files),
            "chunks_count": len(documents),
            "updated_file": updated_file,
            "scope": scope,
            "source_root": str(self.course_root),
        }
        with self.metadata_file.open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, ensure_ascii=False)
        self._log(f"metadata.json сохранен: {self.metadata_file}")
        return metadata

    def _scope_label(
        self,
        module_num: int | None,
        lesson_num: int | None,
        step_num: int | None,
    ) -> str:
        if step_num is not None:
            return f"модуль {module_num}, урок {lesson_num}, шаг {step_num}"
        if lesson_num is not None:
            return f"модуль {module_num}, урок {lesson_num}"
        if module_num is not None:
            return f"модуль {module_num}"
        return "весь курс"

    def _print_statistics(self, metadata: dict[str, Any]) -> None:
        index_chunks = self.vector_index.ntotal if self.vector_index is not None else 0
        self._log("-" * 60)
        self._log(f"Курс: {metadata['course']}")
        self._log(f"Операция: {metadata['operation']}")
        self._log(f"Файлов: {metadata['files_count']}")
        self._log(f"Чанков в chunks.jsonl: {metadata['chunks_count']}")
        self._log(f"Чанков в FAISS: {index_chunks}")
        self._log(f"Тип индекса: {metadata['index_type']}")
        self._log(f"Embedding model: {metadata['embedding_model']}")
        self._log(f"Embedding dim: {metadata['embedding_dim']}")
        self._log("-" * 60)

    def _log(self, message: str) -> None:
        print(message)

    def _add_documents_with_progress(self, documents: list[Document]) -> None:
        if self.vector_index is None:
            raise RuntimeError("Vector index is not initialized")

        total = len(documents)
        if total == 0:
            return

        batches_total = (total + self.INDEX_BATCH_SIZE - 1) // self.INDEX_BATCH_SIZE
        self._log(
            f"Добавление {total} чанков в индекс батчами по {self.INDEX_BATCH_SIZE} "
            f"({batches_total} batch)"
        )
        indexing_started_at = time.perf_counter()
        progress = tqdm(
            range(0, total, self.INDEX_BATCH_SIZE),
            total=batches_total,
            desc="Индексация чанков",
            unit="batch",
        )
        added = 0
        for start in progress:
            batch = documents[start : start + self.INDEX_BATCH_SIZE]
            ids = np.asarray([self._document_id(doc) for doc in batch], dtype=np.int64)
            self._validate_new_ids(ids)

            embed_started_at = time.perf_counter()
            vectors = np.asarray(
                self._get_embeddings().embed_documents([doc.page_content for doc in batch]),
                dtype=np.float32,
            )
            self._log(
                f"Эмбеддинги для batch {start // self.INDEX_BATCH_SIZE + 1}/{batches_total}: "
                f"{self._format_elapsed(embed_started_at)}"
            )

            self.vector_index.add_with_ids(vectors, ids)  # type: ignore[call-arg]
            for doc_id, doc in zip(ids.tolist(), batch):
                self.docstore[int(doc_id)] = doc

            added += len(batch)
            progress.set_postfix_str(f"{added}/{total} chunks")

        self._log(f"Добавление чанков завершено за {self._format_elapsed(indexing_started_at)}")

    def _validate_new_ids(self, ids: np.ndarray) -> None:
        unique_ids = {int(doc_id) for doc_id in ids.tolist()}
        if len(unique_ids) != len(ids):
            raise ValueError("Duplicate chunk ids found inside the batch")

        existing_ids = unique_ids.intersection(self.docstore.keys())
        if existing_ids:
            raise ValueError(
                f"Chunk id collision detected for {len(existing_ids)} chunk(s). "
                "The old scope was not removed correctly."
            )

    def _document_id(self, doc: Document) -> int:
        metadata = doc.metadata
        raw_id = (
            f"{metadata['file_path']}|{metadata['chunk_id']}|"
            f"{metadata.get('content_hash', '')}|{metadata.get('source_mtime', '')}"
        )
        digest = hashlib.blake2b(raw_id.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, byteorder="big", signed=False) & ((1 << 63) - 1)

    def _parse_step_path(self, path: Path) -> dict[str, int]:
        rel = path.relative_to(self.course_root)
        return {
            "module_num": int(rel.parts[0]),
            "lesson_num": int(rel.parts[1]),
            "step_num": int(path.parent.name.split("_")[0]),
        }

    def _format_elapsed(self, started_at: float) -> str:
        elapsed = time.perf_counter() - started_at
        if elapsed < 1:
            return f"{elapsed:.2f} сек"
        if elapsed < 60:
            return f"{elapsed:.1f} сек"
        minutes = int(elapsed // 60)
        seconds = elapsed - minutes * 60
        return f"{minutes} мин {seconds:.1f} сек"
