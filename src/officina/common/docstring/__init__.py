"""Reusable docstring parsing, schema loading, and validation tools.

This package exposes a stable public API composed of parser, schema, and
validation symbols. The ``officina.common.docstring`` surface is the single
entry point.
"""

from . import docstring_parser, docstring_policy, docstring_schema, docstring_validation
from .docstring_parser import *  # noqa: F401,F403
from .docstring_policy import *  # noqa: F401,F403
from .docstring_schema import *  # noqa: F401,F403
from .docstring_validation import *  # noqa: F401,F403

__all__ = sorted(
    {
        *docstring_parser.__all__,
        *docstring_policy.__all__,
        *docstring_schema.__all__,
        *docstring_validation.__all__,
    }
)
