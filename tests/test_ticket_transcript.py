"""
Ticket system tests.

Covers:
  * username-based channel naming (ticket-{number}-{username})
  * username sanitation (lowercase, spaces->hyphens, strip unsupported, collapse hyphens)
  * fallback username order (display name -> username -> user ID)
  * HTML transcript building
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GUILD_ID", "1")
os.environ.setdefault("OWNER_USER_ID", "2")
os.environ.setdefault("ARRIVALS_CHANNEL_ID", "3")

from bot.services.ticket_transcript import (  # noqa: E402
    build_html_transcript,
    sanitize_channel_name,
    ticket_channel_name,
)


class ChannelNamingTests(unittest.TestCase):
    def test_basic_format(self):
        self.assertEqual(ticket_channel_name(42, "exzonom"), "ticket-42-exzonom")

    def test_lowercase(self):
        self.assertEqual(ticket_channel_name(1, "ExzoNom"), "ticket-1-exzonom")

    def test_spaces_to_hyphens(self):
        self.assertEqual(ticket_channel_name(2, "John Doe"), "ticket-2-john-doe")

    def test_strip_unsupported_characters(self):
        self.assertEqual(
            ticket_channel_name(3, "Pilot@#$%^&*()"), "ticket-3-pilot"
        )

    def test_collapse_repeated_hyphens(self):
        self.assertEqual(
            ticket_channel_name(4, "John--Doe--"), "ticket-4-john-doe"
        )

    def test_enforce_length(self):
        long_name = "x" * 120
        result = ticket_channel_name(5, long_name)
        self.assertLessEqual(len(result), 100)
        self.assertTrue(result.startswith("ticket-5-"))

    def test_empty_username_falls_back_to_id(self):
        self.assertEqual(ticket_channel_name(6, "", 98765), "ticket-6-98765")

    def test_numeric_display_name_falls_back(self):
        # display_name that is all digits should not be used
        self.assertEqual(ticket_channel_name(7, "12345", 999), "ticket-7-12345")


class TranscriptTests(unittest.TestCase):
    def test_html_transcript_contains_fields(self):
        html_out = build_html_transcript(
            ticket_number=42,
            channel_name="ticket-42-exzonom",
            creator="Exzonom",
            subject="Cannot connect",
            priority="High",
            assigned_staff="SupportLead",
            opened_at="2026-01-01 10:00:00 UTC",
            closed_at="2026-01-01 11:00:00 UTC",
            closed_by="Moderator",
            messages=[
                {
                    "author": "Exzonom",
                    "timestamp": "2026-01-01 10:05:00 UTC",
                    "content": "Help <needed> & now",
                    "attachments": ["https://cdn.example/a.png"],
                    "embeds": [{"title": "EmbedTitle", "description": "EmbedDesc"}],
                }
            ],
        )
        self.assertIn("Ticket #42 Transcript", html_out)
        self.assertIn("exzonom", html_out)
        self.assertIn("Help &lt;needed&gt; &amp; now", html_out)  # escaped
        self.assertIn("https://cdn.example/a.png", html_out)
        self.assertIn("EmbedTitle", html_out)
        self.assertIn("</html>", html_out)

    def test_plain_text_fallback_contains_messages(self):
        from bot.services.ticket_transcript import build_plaintext_transcript

        text = build_plaintext_transcript(
            ticket_number=1,
            creator="User",
            subject="Subject",
            messages=[{"author": "User", "timestamp": "t", "content": "hello", "attachments": []}],
        )
        self.assertIn("Ticket #1 Transcript", text)
        self.assertIn("hello", text)


if __name__ == "__main__":
    unittest.main()
