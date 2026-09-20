"""Custom-analysis-script plugin system.

Researchers drop a ``.py`` file into ``data/scripts/`` (ships with the package)
or ``~/GaitLabScripts/`` (user-writable). Each file defines a subclass of
``AnalysisScript`` that consumes hub channels and publishes derived channels
back into the hub and, optionally, out to a per-script LSL outlet.
"""

from gaitlab.scripts.api import AnalysisScript, ScriptInput, ScriptOutput

__all__ = ["AnalysisScript", "ScriptInput", "ScriptOutput"]
