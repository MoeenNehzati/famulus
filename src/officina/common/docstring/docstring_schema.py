#!/usr/bin/env python3
"""Compatibility alias for the renamed docstring policy module."""

from __future__ import annotations

import sys

from . import docstring_policy as _docstring_policy
from .docstring_policy import *  # noqa: F401,F403

__all__ = _docstring_policy.__all__

sys.modules[__name__] = _docstring_policy
