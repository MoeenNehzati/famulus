"""Session-local preparation cache for Python source text and syntax trees."""

from __future__ import annotations

import ast
import copy
from pathlib import Path


_CacheValue = tuple[str, ast.Module] | BaseException


class PythonSourceCache:
    """Prepare each exact Python path at most once per repository session.

    Intent
    ------
    Share immutable source text and syntax trees across conformance validators.

    Rationale
    ---------
    Repository validators often discover the same Python files independently.

    Pseudocode
    ----------
    - set owner = repository root
    - set entries = empty exact-path map
    - return cache

    Wraps
    -----
    - none
    """

    def __init__(self, repo_root: Path) -> None:
        """Create an empty cache associated with one repository session.

        Intent
        ------
        Bind lazy preparation lifetime to the staged repository view.

        Rationale
        ---------
        Session ownership prevents prepared trees from leaking across snapshots.

        Pseudocode
        ----------
        - set root = absolute input root
        - set entries = empty map

        Wraps
        -----
        - none
        """
        self.repo_root = Path(repo_root).absolute()
        self._entries: dict[Path, _CacheValue] = {}

    def read_parse(self, path: Path) -> tuple[str, ast.Module]:
        """Return cached UTF-8 source and AST for one exact absolute path.

        Intent
        ------
        Read and parse lazily while preserving logical symlink path identity.

        Rationale
        ---------
        Callers retain their own exception policies, so failures must be replayable.

        Pseudocode
        ----------
        - set key = absolute non-resolved input path
        - if key has a prepared failure:
          - raise a fresh copy of failure
        - if key has prepared source and tree:
          - return source and tree
        - set source = UTF-8 text read from input path
        - set tree = parsed source with input filename
        - if preparation raises a supported failure:
          - set entry = failure
          - raise fresh failure copy
        - set entry = source and tree
        - return source and tree

        Wraps
        -----
        - none
        """
        input_path = Path(path)
        key = input_path.absolute()
        cached = self._entries.get(key)
        if isinstance(cached, BaseException):
            raise copy.copy(cached).with_traceback(None) from None
        if cached is not None:
            return cached
        try:
            source = key.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(key))
        except (OSError, UnicodeError, SyntaxError) as exc:
            self._entries[key] = exc
            raise copy.copy(exc).with_traceback(None) from None
        prepared = source, tree
        self._entries[key] = prepared
        return prepared
