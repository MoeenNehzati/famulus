"""Reusable docstring parsing, policy loading, and validation tools.

This package exposes parser, policy, and validation symbols through one public
entry point. ``docstring_policy`` owns the active standard/configuration model;
``docstring_schema`` remains importable only as a deprecated compatibility alias.
"""

from . import docstring_parser, docstring_policy, docstring_validation
from .docstring_parser import *  # noqa: F401,F403
from .docstring_policy import *  # noqa: F401,F403
from .docstring_validation import *  # noqa: F401,F403

__all__ = sorted(
    {
        *docstring_parser.__all__,
        *docstring_policy.__all__,
        *docstring_validation.__all__,
    }
)
