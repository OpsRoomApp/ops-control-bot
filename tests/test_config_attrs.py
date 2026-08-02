"""
Regression tests guarding config-attribute drift.

History:
  * v0.25.58: config.simbrief_static_id was read by the /randomroute render
    block but never defined in Config -> AttributeError -> the render
    try/except swallowed it into "Route generated, but the result could not
    be rendered."  Also: config.beta_coordinator_role_id was missing and
    crashed the betatester cog at startup.
"""

import os
import re
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
import sys

sys.path.insert(0, str(_SRC))

# Minimal env so Config() constructs.
for k, v in {
    "DISCORD_TOKEN": "x",
    "GUILD_ID": "1",
    "OWNER_USER_ID": "1",
    "ARRIVALS_CHANNEL_ID": "1",
}.items():
    os.environ.setdefault(k, v)

from bot.config import config


class ConfigAttrDriftTests(unittest.TestCase):
    def test_simbrief_static_id_resolves(self):
        # The exact attribute that crashed /randomroute rendering.
        value = config.simbrief_static_id
        self.assertIsInstance(value, str)

    def test_simbrief_user_id_resolves(self):
        value = config.simbrief_user_id
        self.assertIsInstance(value, str)

    def test_beta_coordinator_role_id_resolves(self):
        # Previously missing -> betatester cog failed to load at startup.
        value = config.beta_coordinator_role_id
        self.assertIsInstance(value, int)

    def test_all_referenced_config_attrs_resolve(self):
        """Every config.<attr> used anywhere in src/ must exist on Config."""
        CONFIG_PY = _SRC / "bot" / "config.py"
        text = CONFIG_PY.read_text(encoding="utf-8")
        defined = set(re.findall(r"^    ([a-z_]+): .* = field", text, re.M))
        referenced = set()
        for py in _SRC.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            content = py.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"\bconfig\.([a-z_]+)\b", content):
                referenced.add(m.group(1))
        missing = sorted(referenced - defined)
        self.assertEqual(missing, [], f"config attrs used but not defined: {missing}")


if __name__ == "__main__":
    unittest.main()
