"""Managed launcher package.

Import public operations from :mod:`officina.launchers.agent`.  Keeping package
initialization side-effect free also lets generated launchers execute that
module with ``python -m`` without pre-importing it.
"""

from importlib import import_module

__all__ = [
    "LauncherConfiguration",
    "LauncherConfigurationError",
    "agent_main",
    "build_agent_command",
    "ensure_launcher_configuration",
    "load_launcher_configuration",
    "select_backend",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    module = import_module(".agent", __name__)
    value = module.main if name == "agent_main" else getattr(module, name)
    globals()[name] = value
    return value
