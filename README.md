# Autonomous Spec-to-PR & QA Multi-Agent System

An autonomous multi-agent software engineering system powered by Python 3.11+, LangGraph, Qdrant, FastEmbed/BM25, and MCP. The system reads technical product specifications (PRDs), locates relevant code and documentation via Dual-Index Hybrid RAG, autonomously writes feature code and unit tests, and validates pull requests with QA benchmarks.

---

## Architecture Overview

1. **Dual-Index Hybrid RAG (`src/rag/`)**:
   - `ASTChunker`: AST-based Python parsing extracting classes, functions, docstrings, and imports.
   - `DualHybridIndexer`: Dual Qdrant collections (`project_docs` and `codebase_ast`) fused with BM25 Okapi using Reciprocal Rank Fusion (RRF, $k=60$).
   - `HybridRetriever`: High-level search interfaces for specs and AST code snippets.

2. **Target Repository Environment (`target_repo/`)**:
   - A realistic sandbox service (`OrderPricingService`) with domain models, PRDs, and test suites for agents to practice autonomous feature development and bug fixing.

3. **Multi-Agent Runtime & Persistence**:
   - PostgreSQL 16 + `langgraph-checkpoint-postgres` for persistent agent workflows.
   - Qdrant vector database for semantic memory and codebase indexing.

---

## Quickstart

### 1. Start Infrastructure
```bash
docker compose up -d
```

### 2. Configure Environment
```bash
copy .env.example .env
```

### 3. Run Hybrid RAG Indexing & Test Query
```bash
python -m src.rag.retriever
```

### 4. Run Target Repo Unit Tests
```bash
pytest target_repo/tests/test_services.py
```
