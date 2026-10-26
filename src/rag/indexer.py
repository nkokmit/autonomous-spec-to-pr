import hashlib
import logging
import math
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

try:
    from rank_bm25 import BM25Okapi
except ImportError:

    class BM25Okapi:  # type: ignore[no-redef]
        """Pure-Python BM25Okapi implementation when `rank_bm25` is not installed in the active environment."""

        def __init__(
            self,
            corpus: list[list[str]],
            k1: float = 1.5,
            b: float = 0.75,
            epsilon: float = 0.25,
        ) -> None:
            self.k1 = k1
            self.b = b
            self.epsilon = epsilon
            self.corpus_size = len(corpus)
            self.doc_freqs: list[dict[str, int]] = []
            self.doc_len: list[int] = []
            df: dict[str, int] = {}

            for doc in corpus:
                self.doc_len.append(len(doc))
                frequencies = dict(Counter(doc))
                self.doc_freqs.append(frequencies)
                for token in frequencies:
                    df[token] = df.get(token, 0) + 1

            self.avgdl = sum(self.doc_len) / self.corpus_size if self.corpus_size > 0 else 0.0
            self.idf: dict[str, float] = {}
            idf_sum = 0.0
            negative_idfs: list[str] = []

            for word, freq in df.items():
                idf_val = math.log(self.corpus_size - freq + 0.5) - math.log(freq + 0.5)
                self.idf[word] = idf_val
                idf_sum += idf_val
                if idf_val < 0:
                    negative_idfs.append(word)

            average_idf = idf_sum / len(self.idf) if self.idf else 0.0
            eps = self.epsilon * average_idf
            for word in negative_idfs:
                self.idf[word] = eps

        def get_scores(self, query: list[str]) -> np.ndarray:
            scores = np.zeros(self.corpus_size, dtype=np.float64)
            if self.corpus_size == 0 or self.avgdl == 0:
                return scores
            for q in query:
                q_idf = self.idf.get(q)
                if q_idf is None:
                    continue
                for idx, doc_freq in enumerate(self.doc_freqs):
                    freq = doc_freq.get(q, 0)
                    if freq == 0:
                        continue
                    dl = self.doc_len[idx]
                    denom = freq + self.k1 * (1.0 - self.b + self.b * dl / self.avgdl)
                    scores[idx] += q_idf * (freq * (self.k1 + 1.0)) / denom
            return scores


from src.config import settings
from src.rag.ast_chunker import ASTChunk, ASTChunker

logger = logging.getLogger(__name__)

DOCS_COLLECTION = "project_docs"
CODE_COLLECTION = "codebase_ast"
RRF_K = 60


def _tokenize(text: str) -> list[str]:
    """Tokenize text and code identifiers (including snake_case and CamelCase splitting) for BM25."""
    # Split camelCase/PascalCase into separate tokens as well as standard word tokens
    camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    raw_tokens = re.findall(r"[a-zA-Z0-9_]+", camel_split.lower())
    expanded: list[str] = []
    for tok in raw_tokens:
        expanded.append(tok)
        if "_" in tok:
            expanded.extend(part for part in tok.split("_") if part)
    return expanded


class EmbeddingProvider:
    """
    Provides Dense Embeddings using `fastembed` (`BAAI/bge-small-en-v1.5`, dim=384)
    with a deterministic hash-based n-gram fallback when offline or if ONNX model download fails.
    """

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", dim: int = 384) -> None:
        self.model_name = model_name
        self.dim = dim
        self._fastembed_model: Any | None = None
        self._use_fallback = False
        self._init_model()

    def _init_model(self) -> None:
        try:
            from fastembed import TextEmbedding

            self._fastembed_model = TextEmbedding(model_name=self.model_name)
            # Warmup & verify dimension
            sample = list(self._fastembed_model.embed(["warmup"]))[0]
            self.dim = int(len(sample))
            logger.info("Initialized FastEmbed model '%s' (dim=%d)", self.model_name, self.dim)
        except Exception as exc:
            logger.warning(
                "FastEmbed initialization failed (%s). Falling back to deterministic char/word n-gram embeddings (dim=%d).",
                exc,
                self.dim,
            )
            self._use_fallback = True

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self._use_fallback and self._fastembed_model is not None:
            try:
                embeddings = list(self._fastembed_model.embed(texts))
                return [np.asarray(vec, dtype=np.float32).tolist() for vec in embeddings]
            except Exception as exc:
                logger.warning("FastEmbed runtime error (%s), switching to deterministic fallback.", exc)
                self._use_fallback = True
        return [self._fallback_embed(t) for t in texts]

    def embed_query(self, query: str) -> list[float]:
        return self.embed_documents([query])[0]

    def _fallback_embed(self, text: str) -> list[float]:
        """Feature-hashing embedding over word and character n-grams (L2 normalized)."""
        vec = np.zeros(self.dim, dtype=np.float32)
        tokens = _tokenize(text)
        features = list(tokens)
        # Add bigrams and char 4-grams for semantic robustness across Vietnamese & English
        for i in range(len(tokens) - 1):
            features.append(f"{tokens[i]}_{tokens[i + 1]}")
        cleaned = re.sub(r"\s+", " ", text.lower())
        for i in range(max(0, len(cleaned) - 3)):
            features.append(cleaned[i : i + 4])

        if not features:
            return vec.tolist()

        for feat in features:
            digest = hashlib.md5(feat.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if (digest[4] & 1) == 0 else -1.0
            vec[idx] += sign

        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec.tolist()


class BM25CollectionIndex:
    """In-memory BM25 index synced with a Qdrant collection's payloads."""

    def __init__(self, payloads: list[dict[str, Any]], text_builder: Any) -> None:
        self.payloads = payloads
        self.corpus_tokens = [_tokenize(text_builder(p)) for p in payloads]
        self.bm25 = BM25Okapi(self.corpus_tokens) if self.corpus_tokens else None

    def search(self, query: str, top_k: int) -> list[tuple[str, float, dict[str, Any]]]:
        """Return list of `(doc_id, bm25_score, payload)` sorted by descending BM25 score."""
        if not self.bm25 or not self.payloads:
            return []
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        scores = self.bm25.get_scores(query_tokens)
        ranked_indices = np.argsort(scores)[::-1][:top_k]
        results: list[tuple[str, float, dict[str, Any]]] = []
        for idx in ranked_indices:
            score = float(scores[idx])
            if score <= 0.0:
                continue
            payload = self.payloads[int(idx)]
            doc_id = str(payload["chunk_id"])
            results.append((doc_id, score, payload))
        return results


class DualHybridIndexer:
    """
    Manages two Qdrant collections (`project_docs` and `codebase_ast`) and executes
    Hybrid Search combining Qdrant Dense Vector Search + BM25 Lexical Search via
    Reciprocal Rank Fusion (RRF, k=60).
    """

    def __init__(
        self,
        qdrant_url: str | None = None,
        target_repo_path: Path | str | None = None,
        prefer_in_memory_fallback: bool = True,
    ) -> None:
        self.qdrant_url = qdrant_url or settings.QDRANT_URL
        self.target_repo_path = Path(target_repo_path or settings.TARGET_REPO_PATH).resolve()
        self.embedder = EmbeddingProvider()
        self.client = self._connect_qdrant(self.qdrant_url, prefer_in_memory_fallback)
        self._bm25_indices: dict[str, BM25CollectionIndex] = {}

    @staticmethod
    def _connect_qdrant(url: str, fallback_to_memory: bool) -> QdrantClient:
        """Connect to remote Qdrant server, falling back to local in-memory Qdrant if unreachable."""
        try:
            client = QdrantClient(url=url, timeout=3.0)
            client.get_collections()
            logger.info("Connected to Qdrant server at %s", url)
            return client
        except Exception as exc:
            if not fallback_to_memory:
                raise ConnectionError(f"Unable to connect to Qdrant at {url}: {exc}") from exc
            logger.warning(
                "Qdrant server at %s is unreachable (%s). Using in-memory QdrantClient(':memory:') instance.",
                url,
                exc,
            )
            return QdrantClient(location=":memory:")

    def _ensure_collection(self, collection_name: str) -> None:
        """Create or recreate Qdrant collection with Cosine distance."""
        if self.client.collection_exists(collection_name):
            self.client.delete_collection(collection_name)

        self.client.create_collection(
            collection_name=collection_name,
            vectors_config=qmodels.VectorParams(
                size=self.embedder.dim,
                distance=qmodels.Distance.COSINE,
            ),
        )

    @staticmethod
    def _doc_text_for_indexing(payload: dict[str, Any]) -> str:
        section = payload.get("section_title", "")
        content = payload.get("content", "")
        file_path = payload.get("file_path", "")
        return f"{file_path}\n{section}\n{content}".strip()

    @staticmethod
    def _code_text_for_indexing(payload: dict[str, Any]) -> str:
        symbol_name = payload.get("symbol_name", "")
        symbol_type = payload.get("symbol_type", "")
        file_path = payload.get("file_path", "")
        docstring = payload.get("docstring") or ""
        imports = "\n".join(payload.get("imports") or [])
        content = payload.get("content", "")
        return f"{file_path} {symbol_type} {symbol_name}\n{docstring}\n{imports}\n{content}".strip()

    def chunk_markdown_docs(self, docs_dir: Path | None = None) -> list[dict[str, Any]]:
        """
        Split Markdown files in `./target_repo/docs/*.md` by level-2/level-3 headings (`##` / `###`)
        so each business rule or test case section becomes a coherent, self-contained chunk.
        """
        target_docs_dir = docs_dir or (self.target_repo_path / "docs")
        if not target_docs_dir.exists():
            logger.warning("Docs directory does not exist: %s", target_docs_dir)
            return []

        chunks: list[dict[str, Any]] = []
        for md_file in sorted(target_docs_dir.glob("*.md")):
            try:
                raw_text = md_file.read_text(encoding="utf-8")
            except Exception as exc:
                logger.error("Failed reading markdown file %s: %s", md_file, exc)
                continue

            lines = raw_text.splitlines()
            rel_path = md_file.resolve().relative_to(self.target_repo_path).as_posix()

            current_title = md_file.stem
            current_lines: list[str] = []
            section_start = 1

            def flush_section(end_line: int) -> None:
                body = "\n".join(current_lines).strip()
                if not body:
                    return
                chunk_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_DNS,
                        f"{rel_path}:{current_title}:{section_start}-{end_line}",
                    )
                )
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "file_path": rel_path,
                        "section_title": current_title,
                        "start_line": section_start,
                        "end_line": end_line,
                        "content": body,
                    }
                )

            for idx, line in enumerate(lines, start=1):
                heading_match = re.match(r"^(#{1,3})\s+(.+)$", line.strip())
                if heading_match and current_lines:
                    flush_section(idx - 1)
                    current_title = heading_match.group(2).strip()
                    current_lines = [line]
                    section_start = idx
                else:
                    if heading_match and not current_lines:
                        current_title = heading_match.group(2).strip()
                        section_start = idx
                    current_lines.append(line)

            flush_section(len(lines))

        return chunks

    def index_project_docs(self) -> int:
        """Chunk and index `./target_repo/docs/*.md` into the `project_docs` collection."""
        doc_chunks = self.chunk_markdown_docs()
        self._ensure_collection(DOCS_COLLECTION)
        if not doc_chunks:
            self._bm25_indices[DOCS_COLLECTION] = BM25CollectionIndex([], self._doc_text_for_indexing)
            return 0

        texts = [self._doc_text_for_indexing(chunk) for chunk in doc_chunks]
        vectors = self.embedder.embed_documents(texts)

        points = [
            qmodels.PointStruct(
                id=chunk["chunk_id"],
                vector=vector,
                payload=chunk,
            )
            for chunk, vector in zip(doc_chunks, vectors, strict=True)
        ]
        self.client.upsert(collection_name=DOCS_COLLECTION, points=points)
        self._bm25_indices[DOCS_COLLECTION] = BM25CollectionIndex(doc_chunks, self._doc_text_for_indexing)
        logger.info("Indexed %d markdown chunks into '%s'", len(points), DOCS_COLLECTION)
        return len(points)

    def index_codebase_ast(self) -> int:
        """Parse and index `./target_repo/**/*.py` AST chunks into the `codebase_ast` collection."""
        chunker = ASTChunker(base_dir=self.target_repo_path)
        ast_chunks: list[ASTChunk] = chunker.parse_directory(self.target_repo_path)
        self._ensure_collection(CODE_COLLECTION)
        if not ast_chunks:
            self._bm25_indices[CODE_COLLECTION] = BM25CollectionIndex([], self._code_text_for_indexing)
            return 0

        payloads: list[dict[str, Any]] = []
        for chunk in ast_chunks:
            payload = chunk.to_payload()
            payload["chunk_id"] = str(
                uuid.uuid5(
                    uuid.NAMESPACE_DNS,
                    f"{chunk.file_path}:{chunk.symbol_type}:{chunk.symbol_name}:{chunk.start_line}",
                )
            )
            payloads.append(payload)

        texts = [self._code_text_for_indexing(p) for p in payloads]
        vectors = self.embedder.embed_documents(texts)

        points = [
            qmodels.PointStruct(
                id=payload["chunk_id"],
                vector=vector,
                payload=payload,
            )
            for payload, vector in zip(payloads, vectors, strict=True)
        ]
        self.client.upsert(collection_name=CODE_COLLECTION, points=points)
        self._bm25_indices[CODE_COLLECTION] = BM25CollectionIndex(payloads, self._code_text_for_indexing)
        logger.info("Indexed %d AST chunks into '%s'", len(points), CODE_COLLECTION)
        return len(points)

    def index_all(self) -> dict[str, int]:
        """Build both `project_docs` and `codebase_ast` indices."""
        docs_count = self.index_project_docs()
        code_count = self.index_codebase_ast()
        return {
            DOCS_COLLECTION: docs_count,
            CODE_COLLECTION: code_count,
        }

    def _rebuild_bm25_from_qdrant(self, collection_name: str) -> None:
        """Lazy-load payloads from Qdrant into in-memory BM25 if not yet populated in this process."""
        if not self.client.collection_exists(collection_name):
            return
        records, _ = self.client.scroll(
            collection_name=collection_name,
            limit=10_000,
            with_payload=True,
            with_vectors=False,
        )
        payloads = [dict(rec.payload) for rec in records if rec.payload]
        builder = (
            self._doc_text_for_indexing
            if collection_name == DOCS_COLLECTION
            else self._code_text_for_indexing
        )
        self._bm25_indices[collection_name] = BM25CollectionIndex(payloads, builder)

    def hybrid_search(
        self,
        collection_name: str,
        query: str,
        top_k: int = 5,
        rrf_k: int = RRF_K,
    ) -> list[dict[str, Any]]:
        """
        Perform Hybrid Search combining Qdrant Dense Vector Search and BM25 Okapi
        using Reciprocal Rank Fusion (RRF, default k=60).
        """
        if not query.strip():
            return []

        if not self.client.collection_exists(collection_name):
            self.index_all()

        if collection_name not in self._bm25_indices:
            self._rebuild_bm25_from_qdrant(collection_name)

        candidate_limit = max(top_k * 3, 15)
        query_vector = self.embedder.embed_query(query)

        # 1. Dense Retrieval from Qdrant
        dense_hits: list[tuple[str, float, dict[str, Any]]] = []
        if hasattr(self.client, "query_points"):
            response = self.client.query_points(
                collection_name=collection_name,
                query=query_vector,
                limit=candidate_limit,
                with_payload=True,
            )
            scored_points = response.points
        else:
            scored_points = self.client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=candidate_limit,
                with_payload=True,
            )

        for pt in scored_points:
            payload = dict(pt.payload or {})
            doc_id = str(payload.get("chunk_id") or pt.id)
            dense_hits.append((doc_id, float(pt.score), payload))

        # 2. Sparse / Lexical Retrieval from BM25
        bm25_index = self._bm25_indices.get(collection_name)
        sparse_hits = bm25_index.search(query, top_k=candidate_limit) if bm25_index else []

        # 3. Reciprocal Rank Fusion (RRF) with k=60
        rrf_scores: dict[str, float] = {}
        payload_map: dict[str, dict[str, Any]] = {}
        dense_rank_map: dict[str, int] = {}
        sparse_rank_map: dict[str, int] = {}
        dense_score_map: dict[str, float] = {}
        sparse_score_map: dict[str, float] = {}

        for rank, (doc_id, score, payload) in enumerate(dense_hits, start=1):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank)
            payload_map[doc_id] = payload
            dense_rank_map[doc_id] = rank
            dense_score_map[doc_id] = score

        for rank, (doc_id, score, payload) in enumerate(sparse_hits, start=1):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank)
            payload_map[doc_id] = payload
            sparse_rank_map[doc_id] = rank
            sparse_score_map[doc_id] = score

        sorted_ids = sorted(rrf_scores.keys(), key=lambda d_id: rrf_scores[d_id], reverse=True)[:top_k]

        fused_results: list[dict[str, Any]] = []
        for doc_id in sorted_ids:
            item = dict(payload_map[doc_id])
            item["rrf_score"] = round(rrf_scores[doc_id], 6)
            item["dense_rank"] = dense_rank_map.get(doc_id)
            item["bm25_rank"] = sparse_rank_map.get(doc_id)
            item["dense_score"] = round(dense_score_map.get(doc_id, 0.0), 4)
            item["bm25_score"] = round(sparse_score_map.get(doc_id, 0.0), 4)
            fused_results.append(item)

        return fused_results
