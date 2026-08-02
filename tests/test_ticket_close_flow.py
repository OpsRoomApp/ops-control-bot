"""
v0.25.55 (B1/C1) -- Ticket close flow tests.

Covers:
  * close-reason modal required-field contract
  * hosted-transcript POST success (admin-api) + transcript_url persistence
  * Discord-upload fallback when the admin API is unreachable
  * no-delivery path preserves the ticket (channel kept, status stays open)
  * channel-deletion gating in the close flow (only after "delivered")
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

# Shared DB for the process. Must be set before bot.config is imported.
os.environ["DATABASE_PATH"] = os.path.join(tempfile.gettempdir(), "ops_control_workorder_test.db")
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GUILD_ID", "1")
os.environ.setdefault("OWNER_USER_ID", "2")
os.environ.setdefault("ARRIVALS_CHANNEL_ID", "3")
os.environ.setdefault("PENDING_ACTION_MAX_ATTEMPTS", "3")
os.environ.setdefault("PENDING_ACTION_POLL_SECONDS", "2")

import discord  # noqa: E402

from bot.cogs.ticket_system import CloseReasonModal, TicketActionView  # noqa: E402
from bot.database import get_db, init_db  # noqa: E402
from bot.services.ticket_transcript import close_ticket_with_transcript  # noqa: E402

HOSTED_URL = "https://opsroom.live/transcripts/7"
_SAMPLE_MSGS = [
    {
        "author": "user101 (user101)",
        "timestamp": "2026-01-01 10:05:00 UTC",
        "content": "Please help",
        "attachments": [],
        "embeds": [],
    }
]


def _transcript_config(archive_channel_id: int = 555) -> SimpleNamespace:
    """Config substitute for the transcript service (avoids mutating the frozen Config)."""
    return SimpleNamespace(
        admin_api_base_url="https://admin.example.test",
        admin_api_token="test-token",
        ticket_transcript_channel_id=archive_channel_id,
        transcript_retention_days=14,
    )


class CloseReasonModalTests(unittest.TestCase):
    def test_reason_field_is_required(self):
        """The close-reason modal must require a non-trivial reason (B1)."""
        field = CloseReasonModal.reason
        self.assertTrue(field.required)
        self.assertGreaterEqual(field.min_length, 4)
        self.assertLessEqual(field.max_length, 500)


class TicketCloseFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        db = await get_db()
        await db.execute("DELETE FROM tickets")
        await db.execute("DELETE FROM bugs")
        await db.execute("DELETE FROM logs")
        await db.commit()

    async def _insert_ticket(self, ticket_id: int = 7, channel_id: int = 100, status: str = "open"):
        db = await get_db()
        await db.execute(
            "INSERT INTO tickets (id, user_id, username, category, priority, subject, description, status,"
            " created_at, updated_at, channel_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ticket_id, 101, "user101", "support", "Normal", "Cannot connect", "desc", status,
             "2026-01-01 10:00:00", "2026-01-01 10:00:00", channel_id),
        )
        await db.commit()

    def _bot(self, archive_channel, creator) -> mock.Mock:
        bot = mock.Mock()
        bot.get_channel.return_value = archive_channel
        bot.get_user.return_value = creator
        return bot

    def _channel(self) -> mock.Mock:
        channel = mock.Mock(spec=discord.TextChannel)
        channel.id = 100
        channel.name = "ticket-7-user101"
        channel.send = mock.AsyncMock(return_value=mock.Mock(id=1))
        channel.guild = mock.Mock()
        channel.guild.id = 1
        return channel

    def _closer(self) -> mock.Mock:
        closer = mock.Mock(spec=discord.Member)
        closer.id = 5
        closer.name = "mod"
        closer.display_name = "Moderator"
        closer.mention = "<@5>"
        return closer

    async def _close(self, *, archive_channel, creator, channel, closer):
        return await close_ticket_with_transcript(
            self._bot(archive_channel, creator), channel, closer,
            ticket_id=7, creator_user_id=101, creator_name="user101",
            subject="Cannot connect", priority="High", assigned_staff=None,
            opened_at="2026-01-01 10:00:00 UTC", ticket_number=7,
            close_reason="Resolved by staff",
        )

    # -- Hosted transcript (B1 preferred path) -----------------------------

    async def test_hosted_transcript_success_writes_db(self):
        await self._insert_ticket()
        archive_channel = mock.Mock(spec=discord.TextChannel)
        archive_channel.send = mock.AsyncMock(return_value=mock.Mock(id=555))
        creator = mock.Mock()
        creator.send = mock.AsyncMock()

        with mock.patch("bot.services.ticket_transcript.config", _transcript_config(555)), \
             mock.patch("bot.services.ticket_transcript.fetch_channel_history",
                        new=mock.AsyncMock(return_value=_SAMPLE_MSGS)), \
             mock.patch("bot.services.ticket_transcript._post_transcript_to_admin_api",
                        new=mock.AsyncMock(return_value=(True, "", HOSTED_URL))):
            result = await self._close(
                archive_channel=archive_channel, creator=creator, channel=self._channel(),
                closer=self._closer(),
            )

        self.assertEqual(result["transcript_status"], "delivered")
        self.assertEqual(result["transcript_url"], HOSTED_URL)

        # Archive channel gets a clean embed with the hosted link (no raw HTML file).
        self.assertEqual(archive_channel.send.await_count, 1)
        send_kwargs = archive_channel.send.call_args.kwargs
        self.assertIn("embed", send_kwargs)
        self.assertNotIn("file", send_kwargs)
        self.assertIn("hosted transcript", str(send_kwargs["embed"].to_dict()).lower())

        creator.send.assert_awaited_once()

        db = await get_db()
        cur = await db.execute("SELECT status, close_reason, transcript_url FROM tickets WHERE id=7")
        row = await cur.fetchone()
        self.assertEqual(row["status"], "closed")
        self.assertEqual(row["close_reason"], "Resolved by staff")
        self.assertEqual(row["transcript_url"], HOSTED_URL)

    # -- Fallback: Discord HTML upload when admin-api is unreachable -------

    async def test_fallback_to_discord_upload_when_api_unreachable(self):
        await self._insert_ticket()
        archive_channel = mock.Mock(spec=discord.TextChannel)
        archive_channel.send = mock.AsyncMock(return_value=mock.Mock(id=556))
        creator = mock.Mock()
        creator.send = mock.AsyncMock()

        with mock.patch("bot.services.ticket_transcript.config", _transcript_config(555)), \
             mock.patch("bot.services.ticket_transcript.fetch_channel_history",
                        new=mock.AsyncMock(return_value=_SAMPLE_MSGS)), \
             mock.patch("bot.services.ticket_transcript._post_transcript_to_admin_api",
                        new=mock.AsyncMock(return_value=(False, "admin-api unreachable: Connection refused", None))):
            result = await self._close(
                archive_channel=archive_channel, creator=creator, channel=self._channel(),
                closer=self._closer(),
            )

        # Transcript still durably delivered via the Discord-upload fallback.
        self.assertEqual(result["transcript_status"], "delivered")
        self.assertIsNone(result["transcript_url"])
        send_kwargs = archive_channel.send.call_args.kwargs
        self.assertIn("file", send_kwargs)  # raw HTML upload fallback
        creator.send.assert_awaited_once()

        db = await get_db()
        cur = await db.execute("SELECT status, close_reason, transcript_url FROM tickets WHERE id=7")
        row = await cur.fetchone()
        self.assertEqual(row["status"], "closed")
        self.assertEqual(row["close_reason"], "Resolved by staff")
        self.assertIsNone(row["transcript_url"])

    # -- No delivery path preserves the ticket -----------------------------

    async def test_no_delivery_preserves_ticket(self):
        await self._insert_ticket()
        channel = self._channel()
        closer = self._closer()

        with mock.patch("bot.services.ticket_transcript.config", _transcript_config(0)), \
             mock.patch("bot.services.ticket_transcript.fetch_channel_history",
                        new=mock.AsyncMock(return_value=_SAMPLE_MSGS)), \
             mock.patch("bot.services.ticket_transcript._post_transcript_to_admin_api",
                        new=mock.AsyncMock(return_value=(False, "admin-api unreachable: Connection refused", None))):
            result = await self._close(
                archive_channel=None, creator=None, channel=channel,
                closer=closer,
            )

        self.assertEqual(result["transcript_status"], "failed")
        # Staff notified that archiving failed and the ticket was preserved.
        channel.send.assert_awaited()

        db = await get_db()
        cur = await db.execute("SELECT status, transcript_status FROM tickets WHERE id=7")
        row = await cur.fetchone()
        self.assertEqual(row["status"], "open")  # NOT closed -> channel not deleted

    # -- Channel-deletion gating in the close flow -------------------------

    async def test_channel_deletion_gated_on_transcript_delivery(self):
        await self._insert_ticket()
        view = TicketActionView(ticket_num=42)
        channel = mock.Mock(spec=discord.TextChannel)
        channel.delete = mock.AsyncMock()
        closer = self._closer()
        guild = mock.Mock()
        guild.id = 1
        guild.get_member.return_value = None

        interaction = mock.Mock()
        interaction.channel_id = 100
        interaction.channel = channel
        interaction.user = closer
        interaction.guild = guild
        interaction.guild_id = 1
        interaction.client = mock.Mock()
        interaction.followup = mock.Mock()
        interaction.followup.send = mock.AsyncMock()

        with mock.patch("bot.cogs.ticket_system.close_ticket_with_transcript",
                        new=mock.AsyncMock(return_value={"transcript_status": "failed", "error": "x"})), \
             mock.patch("bot.cogs.ticket_system.log_ticket_closed", new=mock.AsyncMock()), \
             mock.patch("bot.cogs.ticket_system._disable_ticket_controls", new=mock.AsyncMock()):
            await view._close_with_reason(interaction, "Testing")

        channel.delete.assert_not_awaited()
        last_call = interaction.followup.send.call_args_list[-1]
        self.assertIn("preserved", last_call.args[0])

        # Now a successful delivery -> the channel may be deleted.
        channel.delete.reset_mock()
        with mock.patch("bot.cogs.ticket_system.close_ticket_with_transcript",
                        new=mock.AsyncMock(return_value={"transcript_status": "delivered"})), \
             mock.patch("bot.cogs.ticket_system.log_ticket_closed", new=mock.AsyncMock()), \
             mock.patch("bot.cogs.ticket_system._disable_ticket_controls", new=mock.AsyncMock()):
            await view._close_with_reason(interaction, "Testing")

        channel.delete.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
