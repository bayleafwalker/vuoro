"""Historical schema22->25 fixture moved to the current schema25->27 gate.

Past verification records cite this path at their pinned implementation SHA.
The executable upgrade contract now lives in test_schema28_composition_migration.
"""
from .test_schema28_composition_migration import CATALOG_REVISION, CATALOG_TOTAL
