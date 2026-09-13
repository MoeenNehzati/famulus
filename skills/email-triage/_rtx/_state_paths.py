"""Shared mutable-state location for email-triage runtime helpers."""

import os
import sys
from pathlib import Path


def default_state_dir(*, home: Path | None = None) -> Path:
    """Use the explicit triage override or the shared Famulus state directory.

    Intent
    ------
    Give all triage helpers the same state location.

    Rationale
    ---------
    Preserve explicit overrides and the shared platform-specific default.

    Pseudocode
    ----------
    - if EMAIL_TRIAGE_STATE_DIR is nonempty, return its path unchanged
    - otherwise resolve the Famulus email-triage state root for the given home

    Wraps
    -----
    - none
    """
    override = os.environ.get("EMAIL_TRIAGE_STATE_DIR")
    if override:
        return Path(override)
    from officina.common.famulus_paths import resolve_famulus_paths

    return resolve_famulus_paths(
        platform=sys.platform, home=home or Path.home(), environ=os.environ
    ).email_triage_state_root
