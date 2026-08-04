"""Test package bootstrap.

Ensure a temp DATABASE_PATH is the default BEFORE any bot module is
imported. Combined unittest runs share one process and bot.config is
frozen at first import; without this, modules that don't set
DATABASE_PATH themselves (e.g. test_atis) would pin config onto the
real data/ops-control.db and later DB-backed tests would fail against
its legacy tickets schema -- or worse, touch live data.
"""

import os
import tempfile

os.environ.setdefault(
    "DATABASE_PATH",
    os.path.join(tempfile.gettempdir(), "ops_control_unittest_default.db"),
)
