"""
Regression tests pinning the Discord release-note formatting contract.

The formatter in bot.cogs.releases is the *bot* half of a three-way contract
(admin-api discord_webhooks.py, admin ReleaseNotesEditor.jsx). If one side
drifts, the admin-panel Discord preview, the webhook posts and the bot
commands disagree. These tests lock the spec: headings -> bold, bullets kept,
blank lines collapsed, truncation with an ellipsis.
"""

import os
import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

# Minimal env so Config() constructs before bot.config is frozen.
for k, v in {
    "DISCORD_TOKEN": "x",
    "GUILD_ID": "1",
    "OWNER_USER_ID": "1",
    "ARRIVALS_CHANNEL_ID": "1",
}.items():
    os.environ.setdefault(k, v)

from bot.cogs.releases import _version_key, format_notes_for_discord


class FormatNotesForDiscordTests(unittest.TestCase):
    def test_headers_become_bold(self):
        out = format_notes_for_discord("# OPS ROOM v0.25.0\n\n## Highlights\n- one\n- two")
        self.assertEqual(out, "**OPS ROOM v0.25.0**\n**Highlights**\n- one\n- two")

    def test_heading_levels(self):
        self.assertEqual(format_notes_for_discord("### Sub"), "**Sub**")
        self.assertEqual(format_notes_for_discord("## Sub"), "**Sub**")
        self.assertEqual(format_notes_for_discord("# Sub"), "**Sub**")

    def test_blank_lines_collapse_and_emphasis_is_kept(self):
        out = format_notes_for_discord("line1\n\nline2 with **bold**")
        self.assertEqual(out, "line1\nline2 with **bold**")

    def test_truncation_adds_ellipsis(self):
        out = format_notes_for_discord("x" * 500, limit=100)
        self.assertLessEqual(len(out), 101)
        self.assertTrue(out.endswith("\u2026"))
        self.assertTrue(out.startswith("x" * 95))

    def test_short_text_not_truncated(self):
        out = format_notes_for_discord("hello", limit=100)
        self.assertEqual(out, "hello")

    def test_empty_input(self):
        self.assertEqual(format_notes_for_discord(""), "")
        self.assertEqual(format_notes_for_discord(None), "")

    def test_blockquote_is_kept_as_text(self):
        self.assertEqual(format_notes_for_discord("> note"), "note")


class VersionKeyTests(unittest.TestCase):
    def test_plain_version(self):
        self.assertEqual(_version_key("0.25.0"), (0, 25, 0))

    def test_v_prefixed(self):
        self.assertEqual(_version_key("v0.25.0"), (0, 25, 0))

    def test_short_version(self):
        self.assertEqual(_version_key("0.9"), (0, 9, 0))

    def test_empty(self):
        self.assertEqual(_version_key(""), (0, 0, 0))

    def test_comparison_orders_correctly(self):
        self.assertLess(_version_key("0.24.1"), _version_key("0.25.0"))
        self.assertLess(_version_key("0.25.0"), _version_key("0.25.1"))
        self.assertGreater(_version_key("0.26.0"), _version_key("0.25.99"))


if __name__ == "__main__":
    unittest.main()
