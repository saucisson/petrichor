"""Study materials of the experimental protocol (spec §20, phases 3-4).

Each study (pilote, A-D) ships its frozen materials - model, formula
catalog, coverage grid, pre-registration - as versioned data under
``src/petrichor/study/<study>/<version>/``, pinned by content hash. A
change to any material is a new study version, never retro-applied
(spec §20.2).
"""
