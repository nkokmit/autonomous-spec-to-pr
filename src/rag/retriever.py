import json
import logging
import sys
from typing import Any

from src.rag.indexer import CODE_COLLECTION, DOCS_COLLECTION, DualHybridIndexer

logger = logging.getLogger(__name__)

_default_retriever: "HybridRetriever | None" = None


class HybridRetriever:
    """High-level interface for Dual-Index Hybrid RAG over `project_docs` and `codebase_ast`."""

    def __init__(self, indexer: DualHybridIndexer | None = None, auto_index: bool = True) -> None:
        self.indexer = indexer or DualHybridIndexer()
        self._indexed = False
        if auto_index:
            self.ensure_indexed()

    def ensure_indexed(self) -> dict[str, int]:
        """Ensure both Qdrant collections and BM25 indices are populated."""
        counts = self.indexer.index_all()
        self._indexed = True
        return counts

    def search_docs(self, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        """Retrieve top-k relevant documentation sections using Hybrid RRF search."""
        if not self._indexed:
            self.ensure_indexed()
        return self.indexer.hybrid_search(
            collection_name=DOCS_COLLECTION,
            query=query,
            top_k=top_k,
        )

    def search_codebase(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Retrieve top-k relevant Python AST chunks using Hybrid RRF search."""
        if not self._indexed:
            self.ensure_indexed()
        return self.indexer.hybrid_search(
            collection_name=CODE_COLLECTION,
            query=query,
            top_k=top_k,
        )


def _get_default_retriever() -> HybridRetriever:
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = HybridRetriever(auto_index=True)
    return _default_retriever


def search_docs(query: str, top_k: int = 4) -> list[dict[str, Any]]:
    """Search `project_docs` collection for relevant PRD/specification sections."""
    return _get_default_retriever().search_docs(query=query, top_k=top_k)


def search_codebase(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Search `codebase_ast` collection for relevant Python classes and functions."""
    return _get_default_retriever().search_codebase(query=query, top_k=top_k)


if __name__ == "__main__":
    # Configure UTF-8 output for Windows console
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )

    print("=" * 80)
    print("🚀 INITIALIZING DUAL-INDEX HYBRID RAG FOR ./target_repo")
    print("=" * 80)

    retriever = _get_default_retriever()

    doc_query = "Quy tắc giảm giá hạng VIP tối đa 500k và loại trừ hàng Flash Sale"
    print(f"\n📚 [QUERY DOCS]: {doc_query}")
    print("-" * 80)
    doc_results = search_docs(doc_query, top_k=3)
    for idx, hit in enumerate(doc_results, start=1):
        print(
            f"[{idx}] {hit['file_path']} (Lines {hit['start_line']}-{hit['end_line']}) "
            f"| Section: {hit['section_title']} "
            f"| RRF={hit['rrf_score']} (Dense rank={hit['dense_rank']}, BM25 rank={hit['bm25_rank']})"
        )
        preview = hit["content"].splitlines()[:4]
        print("    " + "\n    ".join(preview))

    code_query = "calculate_average_item_price ZeroDivisionError OrderPricingService"
    print(f"\n💻 [QUERY CODEBASE]: {code_query}")
    print("-" * 80)
    code_results = search_codebase(code_query, top_k=4)
    for idx, hit in enumerate(code_results, start=1):
        print(
            f"[{idx}] {hit['file_path']} :: {hit['symbol_name']} ({hit['symbol_type']}) "
            f"Lines {hit['start_line']}-{hit['end_line']} "
            f"| RRF={hit['rrf_score']} (Dense rank={hit['dense_rank']}, BM25 rank={hit['bm25_rank']})"
        )
        print(f"    Imports: {json.dumps(hit['imports'])}")
        print(f"    Docstring: {hit['docstring']!r}")

