"""Discover ``AnalysisScript`` subclasses in whitelisted folders.

Two locations are searched, in order:

1. ``data/scripts/`` inside the installed package (reference scripts).
2. ``~/GaitLabScripts/`` (user drop-in folder, survives package upgrades).

If both folders define scripts with the same ``id`` the user copy wins so
users can override a shipped reference implementation.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Type

from gaitlab.scripts.api import AnalysisScript
from gaitlab.scripts.offline_api import (
    FunctionOfflineAnalysis,
    NumpyOfflineAnalysis,
    OfflineAnalysis,
    PandasOfflineAnalysis,
    build_function_adapter,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredScript:
    """A script class that survived import + validation."""

    cls: Type[AnalysisScript]
    source_path: Path
    origin: str  # "package" | "user"

    @property
    def id(self) -> str:
        return self.cls.id

    @property
    def display_name(self) -> str:
        return self.cls.display_name or self.cls.__name__


def _iter_python_files(folder: Path) -> Iterable[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix == ".py" and not p.name.startswith("_"))


def _load_module(path: Path) -> object | None:
    """Import ``path`` as a standalone module; return the module or None on error."""
    mod_name = f"_gaitlab_userscript_{path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            log.warning("Could not build import spec for %s", path)
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception as exc:
        log.warning("Failed to import script %s: %s", path, exc)
        return None


def _collect_subclasses(module: object) -> list[Type[AnalysisScript]]:
    """Return ``AnalysisScript`` subclasses defined directly in ``module``."""
    found: list[Type[AnalysisScript]] = []
    for _name, obj in inspect.getmembers(module, inspect.isclass):
        if obj is AnalysisScript:
            continue
        if not issubclass(obj, AnalysisScript):
            continue
        # Only pick up classes defined in this file, not re-exports
        if getattr(obj, "__module__", None) != getattr(module, "__name__", None):
            continue
        if not obj.id or not obj.id.strip():
            log.warning(
                "Ignoring %s from %s: missing class attribute `id`.",
                obj.__name__,
                getattr(module, "__file__", "?"),
            )
            continue
        if not obj.outputs:
            log.warning(
                "Ignoring script %r from %s: no outputs declared.",
                obj.id,
                getattr(module, "__file__", "?"),
            )
            continue
        found.append(obj)
    return found


def discover_scripts(
    package_folder: Path | None,
    user_folder: Path | None,
) -> list[DiscoveredScript]:
    """Discover all valid analysis scripts. User folder overrides package folder on id collision."""
    by_id: dict[str, DiscoveredScript] = {}

    def _scan(folder: Path | None, origin: str) -> None:
        if folder is None:
            return
        for py in _iter_python_files(folder):
            module = _load_module(py)
            if module is None:
                continue
            for cls in _collect_subclasses(module):
                key = cls.id.strip().lower()
                if key in by_id and origin == "package":
                    # User copy already took this id — don't overwrite it.
                    continue
                by_id[key] = DiscoveredScript(cls=cls, source_path=py, origin=origin)

    # User first so package doesn't stomp on user overrides.
    _scan(user_folder, "user")
    _scan(package_folder, "package")

    return sorted(by_id.values(), key=lambda d: d.display_name.lower())


# ------------------------------------------------------------------ offline

# Base classes we deliberately DON'T want to pick up as concrete analyses
# — they exist to be subclassed, not enabled.
_OFFLINE_ABSTRACT_BASES = {
    OfflineAnalysis,
    NumpyOfflineAnalysis,
    PandasOfflineAnalysis,
    FunctionOfflineAnalysis,
}


@dataclass(frozen=True)
class DiscoveredOfflineAnalysis:
    """One offline analysis (class-based or function-based) that survived
    import + validation. ``cls`` is either a researcher-written subclass
    of NumpyOfflineAnalysis / PandasOfflineAnalysis, or an auto-generated
    adapter wrapping a top-level ``run`` function."""

    cls: Type[OfflineAnalysis]
    source_path: Path
    origin: str  # "package" | "user"

    @property
    def id(self) -> str:
        return self.cls.id

    @property
    def display_name(self) -> str:
        return self.cls.display_name or self.cls.__name__

    @property
    def flavor(self) -> str:
        return self.cls.flavor


def _collect_offline_from_module(
    module: object,
) -> list[Type[OfflineAnalysis]]:
    """Find every valid OfflineAnalysis in a module — subclasses plus
    the function-based form (module-level ``run`` + metadata constants).

    Skips the abstract base classes so importing offline_api.py itself
    doesn't accidentally register three ghost analyses."""
    found: list[Type[OfflineAnalysis]] = []
    mod_name = getattr(module, "__name__", None)
    for _name, obj in inspect.getmembers(module, inspect.isclass):
        if obj in _OFFLINE_ABSTRACT_BASES:
            continue
        if not isinstance(obj, type) or not issubclass(obj, OfflineAnalysis):
            continue
        if getattr(obj, "__module__", None) != mod_name:
            continue
        if not obj.id or not obj.id.strip():
            log.warning(
                "Ignoring offline analysis %s from %s: missing class attribute `id`.",
                obj.__name__,
                getattr(module, "__file__", "?"),
            )
            continue
        if not obj.outputs:
            log.warning(
                "Ignoring offline analysis %r from %s: no outputs declared.",
                obj.id,
                getattr(module, "__file__", "?"),
            )
            continue
        if obj.flavor not in ("numpy", "pandas"):
            log.warning(
                "Ignoring offline analysis %r from %s: `flavor` must be "
                "'numpy' or 'pandas', got %r.",
                obj.id,
                getattr(module, "__file__", "?"),
                obj.flavor,
            )
            continue
        found.append(obj)

    # Function-based form: a module-level `run` callable + metadata.
    run_fn = getattr(module, "run", None)
    if callable(run_fn):
        try:
            adapter = build_function_adapter(module, run_fn)
        except Exception as exc:
            log.warning(
                "Function-based offline script in %s could not be adapted: %s",
                getattr(module, "__file__", "?"),
                exc,
            )
            adapter = None
        if adapter is not None:
            found.append(adapter)
    return found


def discover_offline_analyses(
    package_folder: Path | None,
    user_folder: Path | None,
) -> list[DiscoveredOfflineAnalysis]:
    """Same folders and precedence as ``discover_scripts`` — user
    overrides package on id collision — but collects OfflineAnalysis
    subclasses (both flavors) and function-based analyses."""
    by_id: dict[str, DiscoveredOfflineAnalysis] = {}

    def _scan(folder: Path | None, origin: str) -> None:
        if folder is None:
            return
        for py in _iter_python_files(folder):
            module = _load_module(py)
            if module is None:
                continue
            for cls in _collect_offline_from_module(module):
                key = cls.id.strip().lower()
                if key in by_id and origin == "package":
                    continue
                by_id[key] = DiscoveredOfflineAnalysis(
                    cls=cls, source_path=py, origin=origin
                )

    _scan(user_folder, "user")
    _scan(package_folder, "package")
    return sorted(by_id.values(), key=lambda d: d.display_name.lower())
