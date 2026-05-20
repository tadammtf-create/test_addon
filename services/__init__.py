"""AI Toolkit service layer package.

The service layer contains all bpy-free business logic for the AI Toolkit:
provider adapters, job execution, request/response data classes, settings
storage, history persistence, and scene analysis.

Per Requirement 13.1, no module under ``services/`` may import any of the
Blender-distributed modules ``bpy``, ``bpy_extras``, ``mathutils``, ``bgl``,
``gpu``, ``bmesh``, or ``blf``. This invariant is what allows the service
layer to be unit-tested in a plain Python 3 interpreter on hosts where
Blender is not installed (Requirements 13.4, 13.6).

This module configures the single addon-wide logger named ``ai_toolkit``
using only the Python standard library. UI-layer code should retrieve the
same logger via ``logging.getLogger("ai_toolkit")`` so that every log
record from the addon shares one namespace.
"""

import logging

# Addon-wide logger. Both service-layer and ui-layer modules should fetch
# this same logger via ``logging.getLogger("ai_toolkit")``.
logger = logging.getLogger("ai_toolkit")
logger.setLevel(logging.INFO)

# Don't propagate to the root logger; Blender's root configuration is
# unpredictable across versions and we don't want duplicate records.
logger.propagate = False

# Guard against duplicate handlers when this module is re-imported (which
# happens routinely when a Blender addon is reloaded).
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("[%(name)s] %(levelname)s %(message)s")
    )
    logger.addHandler(_handler)

__all__ = ["logger"]
