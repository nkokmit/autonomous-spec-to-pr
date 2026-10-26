import ast
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

SymbolType = Literal["class", "function", "async_function", "module"]


@dataclass
class ASTChunk:
    """Represents a single code chunk extracted via Python AST parsing."""

    content: str
    file_path: str
    symbol_name: str
    symbol_type: SymbolType
    start_line: int
    end_line: int
    imports: list[str] = field(default_factory=list)
    docstring: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Serialize chunk into a Qdrant-compatible payload dictionary."""
        return asdict(self)


class ASTChunker:
    """Parses Python source files using the standard `ast` module into semantic chunks."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base_dir = Path(base_dir).resolve() if base_dir else None

    def _format_file_path(self, file_path: Path) -> str:
        resolved = file_path.resolve()
        if self.base_dir:
            try:
                return resolved.relative_to(self.base_dir).as_posix()
            except ValueError:
                pass
        return resolved.as_posix()

    @staticmethod
    def _extract_imports(tree: ast.Module, source_lines: list[str]) -> list[str]:
        """Extract all top-level import and from-import statements from the module."""
        imports: list[str] = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                start = (node.lineno or 1) - 1
                end = node.end_lineno or (start + 1)
                stmt = "\n".join(source_lines[start:end]).strip()
                if stmt:
                    imports.append(stmt)
        return imports

    @staticmethod
    def _slice_source(source_lines: list[str], node: ast.AST) -> tuple[str, int, int]:
        """Extract exact source lines including decorators for a given AST node."""
        decorator_lines = [
            getattr(dec, "lineno", node.lineno)
            for dec in getattr(node, "decorator_list", [])
            if hasattr(dec, "lineno")
        ]
        start_line = min([ getattr(node, "lineno", 1), *decorator_lines ])
        end_line = getattr(node, "end_lineno", start_line)
        snippet = "\n".join(source_lines[start_line - 1 : end_line])
        return snippet, start_line, end_line

    def parse_file(self, file_path: Path | str) -> list[ASTChunk]:
        """
        Parse a single `.py` file and extract `ClassDef`, `FunctionDef`, and `AsyncFunctionDef`
        nodes as standalone chunks. Also extracts methods inside classes as individual chunks
        with qualified `ClassName.method_name` symbol names alongside the full class chunk.
        """
        path = Path(file_path)
        if not path.is_file() or path.suffix != ".py":
            return []

        try:
            source_text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                source_text = path.read_text(encoding="latin-1")
            except Exception as exc:
                logger.error("Failed to read file %s: %s", path, exc)
                return []
        except OSError as exc:
            logger.error("OS error reading file %s: %s", path, exc)
            return []

        if not source_text.strip():
            return []

        try:
            tree = ast.parse(source_text, filename=str(path))
        except SyntaxError as exc:
            logger.warning("SyntaxError while parsing %s: %s", path, exc)
            return []

        source_lines = source_text.splitlines()
        rel_path = self._format_file_path(path)
        imports = self._extract_imports(tree, source_lines)
        chunks: list[ASTChunk] = []

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                content, start_line, end_line = self._slice_source(source_lines, node)
                chunks.append(
                    ASTChunk(
                        content=content,
                        file_path=rel_path,
                        symbol_name=node.name,
                        symbol_type="class",
                        start_line=start_line,
                        end_line=end_line,
                        imports=imports,
                        docstring=ast.get_docstring(node),
                    )
                )

                # Also extract individual methods within the class for granular retrieval
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        m_content, m_start, m_end = self._slice_source(source_lines, child)
                        m_type: SymbolType = (
                            "async_function"
                            if isinstance(child, ast.AsyncFunctionDef)
                            else "function"
                        )
                        chunks.append(
                            ASTChunk(
                                content=m_content,
                                file_path=rel_path,
                                symbol_name=f"{node.name}.{child.name}",
                                symbol_type=m_type,
                                start_line=m_start,
                                end_line=m_end,
                                imports=imports,
                                docstring=ast.get_docstring(child),
                            )
                        )

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                content, start_line, end_line = self._slice_source(source_lines, node)
                sym_type: SymbolType = (
                    "async_function"
                    if isinstance(node, ast.AsyncFunctionDef)
                    else "function"
                )
                chunks.append(
                    ASTChunk(
                        content=content,
                        file_path=rel_path,
                        symbol_name=node.name,
                        symbol_type=sym_type,
                        start_line=start_line,
                        end_line=end_line,
                        imports=imports,
                        docstring=ast.get_docstring(node),
                    )
                )

        # Fallback: if a module has executable code/constants but no classes or functions
        if not chunks and source_text.strip():
            chunks.append(
                ASTChunk(
                    content=source_text,
                    file_path=rel_path,
                    symbol_name=path.stem,
                    symbol_type="module",
                    start_line=1,
                    end_line=len(source_lines),
                    imports=imports,
                    docstring=ast.get_docstring(tree),
                )
            )

        return chunks

    def parse_directory(self, directory: Path | str) -> list[ASTChunk]:
        """Recursively parse all Python files in a directory."""
        root = Path(directory).resolve()
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"Target directory does not exist: {root}")

        if self.base_dir is None:
            self.base_dir = root

        ignored_dirs = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}
        all_chunks: list[ASTChunk] = []

        for py_file in sorted(root.rglob("*.py")):
            if any(part in ignored_dirs for part in py_file.parts):
                continue
            file_chunks = self.parse_file(py_file)
            all_chunks.extend(file_chunks)

        return all_chunks


def chunk_python_repo(repo_path: Path | str) -> list[ASTChunk]:
    """Convenience helper to parse all `.py` files in a target repository."""
    chunker = ASTChunker(base_dir=repo_path)
    return chunker.parse_directory(repo_path)

