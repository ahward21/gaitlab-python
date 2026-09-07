"""Runtime for offline analyses.

Responsibilities:

* **Registry**: keep the discovered analysis classes plus the researcher's
  enable/disable state. Parallels ``ScriptRegistry`` but simpler — offline
  analyses have no threads, no timer, no LSL outlet.
* **Runner**: given a completed :class:`SessionComputeResult` from the
  session player, execute each enabled analysis once and return its
  result. Handles the numpy-vs-pandas flavor dispatch and the
  DataFrame-vs-dict return normalisation, so callers see a uniform
  ``dict[str, np.ndarray]`` per analysis regardless of what the
  researcher wrote.
* **Isolation**: an exception in one analysis is caught, logged, and
  packaged into the result; the batch continues so a bad analysis
  doesn't hide the ones that did work.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Type

import numpy as np

from gaitlab.scripts.offline_api import OfflineAnalysis

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OfflineAnalysisResult:
    """One analysis's contribution to a batch. Fields are ``None`` when
    the run failed — check ``error`` for the message."""

    analysis_id: str
    display_name: str
    flavor: str
    outputs: dict[str, np.ndarray] = field(default_factory=dict)
    duration_sec: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class OfflineRegistry:
    """Holds discovered analyses and the researcher's enable state.

    Enable state is a plain set of ids. Unlike :class:`ScriptRegistry`,
    there is no per-instance state to keep — analyses are instantiated
    fresh each run so previous outputs never pollute the next batch.
    """

    def __init__(self) -> None:
        self._discovered: dict[str, Type[OfflineAnalysis]] = {}
        self._enabled: set[str] = set()

    # ---- discovery ----

    def register_class(self, cls: Type[OfflineAnalysis]) -> None:
        if not cls.id:
            return
        self._discovered[cls.id.lower()] = cls

    def register_classes(self, classes: list[Type[OfflineAnalysis]]) -> None:
        for c in classes:
            self.register_class(c)

    def clear_discovered(self) -> None:
        self._discovered.clear()
        # Enable state is preserved across rescans intentionally: if a
        # user removes then re-adds a script file with the same id, they
        # get their previous checkbox back.

    def discovered(self) -> list[Type[OfflineAnalysis]]:
        return [self._discovered[k] for k in sorted(self._discovered.keys())]

    def get_class(self, analysis_id: str) -> Type[OfflineAnalysis] | None:
        return self._discovered.get((analysis_id or "").lower())

    # ---- enable state ----

    def enable(self, analysis_id: str) -> None:
        key = (analysis_id or "").lower()
        if key in self._discovered:
            self._enabled.add(key)

    def disable(self, analysis_id: str) -> None:
        self._enabled.discard((analysis_id or "").lower())

    def set_enabled(self, analysis_id: str, enabled: bool) -> None:
        (self.enable if enabled else self.disable)(analysis_id)

    def is_enabled(self, analysis_id: str) -> bool:
        return (analysis_id or "").lower() in self._enabled

    def enabled_ids(self) -> list[str]:
        return sorted(self._enabled & set(self._discovered.keys()))

    # ---- execution ----

    def run_all(
        self,
        times: np.ndarray,
        input_series: dict[str, np.ndarray],
    ) -> list[OfflineAnalysisResult]:
        """Execute every enabled analysis against the given trace.

        ``times`` and ``input_series`` come from a completed
        :class:`SessionComputeResult`. Each enabled analysis' declared
        inputs are looked up in ``input_series`` by ``default_channel``;
        missing channels become all-zero arrays of length ``len(times)``
        so a stale binding produces an obviously-zero output rather than
        a KeyError."""
        results: list[OfflineAnalysisResult] = []
        for aid in self.enabled_ids():
            cls = self._discovered[aid]
            results.append(self._run_one(cls, times, input_series))
        return results

    def _run_one(
        self,
        cls: Type[OfflineAnalysis],
        times: np.ndarray,
        input_series: dict[str, np.ndarray],
    ) -> OfflineAnalysisResult:
        try:
            instance = cls()
        except Exception as exc:
            log.exception("Offline analysis %r failed to instantiate", cls.id)
            return OfflineAnalysisResult(
                analysis_id=cls.id,
                display_name=cls.display_name or cls.__name__,
                flavor=cls.flavor,
                error=f"instantiate failed: {type(exc).__name__}: {exc}",
            )

        # Build the arrays dict keyed by input symbol (not channel id) so
        # the researcher sees the names they declared in ``inputs``.
        symbol_arrays: dict[str, np.ndarray] = {}
        n = len(times)
        for inp in cls.inputs:
            channel = inp.default_channel or ""
            if channel and channel in input_series:
                arr = np.asarray(input_series[channel], dtype=np.float64)
            else:
                if channel:
                    log.warning(
                        "Offline analysis %r: input channel %r not present in "
                        "session — passing zeros.",
                        cls.id,
                        channel,
                    )
                arr = np.zeros(n, dtype=np.float64)
            symbol_arrays[inp.symbol] = arr

        started = time.perf_counter()
        try:
            if cls.flavor == "numpy":
                raw = instance.run(np.asarray(times, dtype=np.float64), symbol_arrays)
            elif cls.flavor == "pandas":
                # Lazy import — pandas is only a dep when a pandas-flavor
                # analysis actually runs. Keeps app cold-start snappy.
                import pandas as pd

                df = pd.DataFrame(symbol_arrays, index=pd.Index(times, name="time_sec"))
                raw = instance.run(df)
            else:
                raise ValueError(f"unknown flavor {cls.flavor!r}")
        except Exception as exc:
            log.exception("Offline analysis %r raised", cls.id)
            return OfflineAnalysisResult(
                analysis_id=cls.id,
                display_name=cls.display_name or cls.__name__,
                flavor=cls.flavor,
                duration_sec=time.perf_counter() - started,
                error=f"{type(exc).__name__}: {exc}",
            )
        duration = time.perf_counter() - started

        outputs = _normalise_outputs(raw, cls)
        return OfflineAnalysisResult(
            analysis_id=cls.id,
            display_name=cls.display_name or cls.__name__,
            flavor=cls.flavor,
            outputs=outputs,
            duration_sec=duration,
        )


def _normalise_outputs(raw: object, cls: Type[OfflineAnalysis]) -> dict[str, np.ndarray]:
    """Coerce whatever the researcher returned into ``dict[str, np.ndarray]``.

    Accepts a dict, a pandas.DataFrame, a pandas.Series (single-output
    case), or a numpy structured array. Extra keys the analysis didn't
    declare in ``outputs`` are kept (many analyses helpfully return
    additional diagnostic columns); declared outputs that were omitted
    are logged and skipped."""
    result: dict[str, np.ndarray] = {}
    if raw is None:
        return result

    # DataFrame / Series duck-typing without importing pandas: check for
    # the attributes we need.
    if hasattr(raw, "to_dict") and hasattr(raw, "columns"):
        # pandas.DataFrame
        for col in raw.columns:
            result[str(col)] = np.asarray(raw[col].values)
    elif hasattr(raw, "name") and hasattr(raw, "values") and not isinstance(raw, dict):
        # pandas.Series
        name = getattr(raw, "name", None) or (cls.outputs[0].symbol if cls.outputs else "value")
        result[str(name)] = np.asarray(raw.values)
    elif isinstance(raw, dict):
        for k, v in raw.items():
            result[str(k)] = np.asarray(v)
    else:
        log.warning(
            "Offline analysis %r returned an unsupported type %s — expected "
            "dict, pandas.DataFrame or pandas.Series.",
            cls.id,
            type(raw).__name__,
        )
    return result
