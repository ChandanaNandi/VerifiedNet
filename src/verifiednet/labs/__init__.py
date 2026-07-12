"""Lab backends. Gate 3 ships the behavioral interface only (no live backend).

The ``LabBackend`` protocol lives here per Gate 2.5 W2 (interfaces live in
their owning packages).
"""

from verifiednet.labs.backend import LabBackend

__all__ = ["LabBackend"]
