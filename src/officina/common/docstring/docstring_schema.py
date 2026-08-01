#!/usr/bin/env python3
"""Deprecated compatibility alias for ``docstring_policy``.

New internal code should import policy dataclasses and loaders from
``officina.common.docstring.docstring_policy``. This module remains only so
older callers that import ``docstring_schema`` continue to receive the same
objects.
"""

from __future__ import annotations

import sys

from . import docstring_policy as _docstring_policy
from .docstring_policy import *  # noqa: F401,F403

__all__ = _docstring_policy.__all__

sys.modules[__name__] = _docstring_policy
