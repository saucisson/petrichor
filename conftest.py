"""Shared pytest configuration: the hypothesis budget of a run (ADR-0012).

The ``default`` profile keeps the full sampling depth (local runs, pushes
to ``main``). The ``ci`` profile is the pull-request budget: fewer
examples per property test, so the merge gate stays fast. ``ci.yml``
selects the profile through the ``HYPOTHESIS_PROFILE`` environment
variable; an explicit ``@settings(max_examples=...)`` on a test always
wins over the profile.
"""

import os

from hypothesis import settings

settings.register_profile("ci", max_examples=40)

settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
