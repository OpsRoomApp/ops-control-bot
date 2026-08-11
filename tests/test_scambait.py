"""
Scambait channel tests.

Covers:
  * non-staff message in the scambait channel -> soft-ban (timeout) + DM + case
  * message in any other channel -> untouched
  * staff are exempt from the scambait
  * /scambait-warning posts the standing notice (staff) / is blocked (non-staff)

Note: the shared `config` singleton may have been constructed earlier in the
test process without SCAMBAIT_CHANNEL_ID set, so these tests patch
bot.cogs.moderation.config directly rather than relying on env import order.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

os.environ["DATABASE_PATH"] = os.path.join(tempfile.gettempdir(), "ops_control_scambait_test.db")
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GUILD_ID", "1")
os.environ.setdefault("OWNER_USER_ID", "2")
os.environ.setdefault("ARRIVALS_CHANNEL_ID", "3")

from bot.cogs.moderation import Moderation  # noqa: E402
from bot.database import get_db, init_db  # noqa: E402


def _scambait_config():
    return mock.Mock(
        scambait_channel_id=9999,
        scambait_timeout_minutes=30,
        appeal_form_url="https://opsroom.live/appeal",
        mod_log_channel_id=0,
    )


class FakeMember:
    bot = False

    def __init__(self, uid: int, name: str = "user"):
        self.id = uid
        self.name = name
        self.dms: list[str] = []
        self.timeout_calls: list = []

    @property
    def mention(self):
        return f"<@{self.id}>"

    async def send(self, content=None, **kwargs):
        self.dms.append(content)
        return mock.Mock()

    async def timeout(self, until=None, reason=None):
        self.timeout_calls.append(until)


class FakeChannel:
    def __init__(self, cid: int, name: str):
        self.id = cid
        self.name = name
        self.sent: list = []

    async def send(self, content=None, embed=None, **kwargs):
        self.sent.append({"content": content, "embed": embed})


class FakeGuild:
    def __init__(self, gid: int = 1, channels=None):
        self.id = gid
        self.name = "Test Guild"
        self.channels = channels or {}

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def get_member(self, user_id):
        return None


class FakeResponse:
    def __init__(self):
        self.messages = []

    async def send_message(self, content=None, ephemeral=False, **kwargs):
        self.messages.append({"content": content, "ephemeral": ephemeral})

    def is_done(self):
        return bool(self.messages)


class FakeFollowup:
    def __init__(self, response):
        self._response = response

    async def send(self, content=None, ephemeral=False, **kwargs):
        self._response.messages.append({"content": content, "ephemeral": ephemeral})


class FakeInteraction:
    def __init__(self, user, guild):
        self.user = user
        self.guild = guild
        self.response = FakeResponse()
        self.followup = FakeFollowup(self.response)


class FakeMessage:
    def __init__(self, author, guild, channel, content="hello"):
        self.author = author
        self.guild = guild
        self.channel = channel
        self.content = content
        self.mentions = []
        self.role_mentions = []
        self.deleted = False

    async def delete(self):
        self.deleted = True


class ScambaitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        db = await get_db()
        await db.execute("DELETE FROM moderation_cases")
        await db.execute("DELETE FROM automod_config")
        await db.commit()
        bot = mock.Mock()
        bot.user.id = 0
        self.cog = Moderation(bot)
        self.scambait_channel = FakeChannel(9999, "scambait")
        self.other_channel = FakeChannel(1111, "general")
        self.guild = FakeGuild(
            1, channels={9999: self.scambait_channel, 1111: self.other_channel}
        )
        # Patch the config reference used inside moderation.py so tests are
        # independent of module import order across the whole suite.
        self.config_patcher = mock.patch(
            "bot.cogs.moderation.config", new=_scambait_config()
        )
        self.config_patcher.start()
        self.addCleanup(self.config_patcher.stop)

    def _msg(self, uid, channel):
        return FakeMessage(FakeMember(uid), self.guild, channel)

    async def test_scambait_message_soft_bans_and_warns(self):
        msg = self._msg(700, self.scambait_channel)
        with mock.patch("bot.cogs.moderation.is_staff", new=mock.AsyncMock(return_value=False)):
            await self.cog.on_message(msg)
        self.assertTrue(msg.deleted)
        self.assertEqual(len(msg.author.timeout_calls), 1)
        self.assertEqual(len(msg.author.dms), 1)
        self.assertIn("restricted", msg.author.dms[0].lower())
        self.assertIn("appeal", msg.author.dms[0].lower())
        self.assertIn("30", msg.author.dms[0])
        self.assertEqual(len(self.scambait_channel.sent), 1)
        self.assertIsNotNone(self.scambait_channel.sent[0]["embed"])
        db = await get_db()
        cur = await db.execute(
            "SELECT action_type, expires_at FROM moderation_cases WHERE user_id=700"
        )
        row = await cur.fetchone()
        self.assertEqual(row["action_type"], "SCAMBAIT_TIMEOUT")
        self.assertIsNotNone(row["expires_at"])

    async def test_other_channel_untouched(self):
        msg = self._msg(701, self.other_channel)
        with mock.patch("bot.cogs.moderation.is_staff", new=mock.AsyncMock(return_value=False)):
            await self.cog.on_message(msg)
        self.assertFalse(msg.deleted)
        self.assertEqual(msg.author.timeout_calls, [])
        self.assertEqual(msg.author.dms, [])
        db = await get_db()
        cur = await db.execute("SELECT COUNT(*) AS c FROM moderation_cases WHERE user_id=701")
        self.assertEqual((await cur.fetchone())["c"], 0)

    async def test_staff_exempt(self):
        msg = self._msg(702, self.scambait_channel)
        with mock.patch("bot.cogs.moderation.is_staff", new=mock.AsyncMock(return_value=True)):
            await self.cog.on_message(msg)
        self.assertFalse(msg.deleted)
        self.assertEqual(msg.author.timeout_calls, [])
        self.assertEqual(len(self.scambait_channel.sent), 0)

    async def test_warning_command_staff(self):
        staff = FakeMember(2)
        interaction = FakeInteraction(staff, self.guild)
        with mock.patch("bot.cogs.moderation.is_staff", new=mock.AsyncMock(return_value=True)):
            await self.cog.scambait_warning_cmd.callback(self.cog, interaction)
        self.assertEqual(len(self.scambait_channel.sent), 1)
        self.assertIsNotNone(self.scambait_channel.sent[0]["embed"])
        self.assertIn("Warning posted", interaction.response.messages[0]["content"])
        self.assertTrue(interaction.response.messages[0]["ephemeral"])

    async def test_warning_command_non_staff_blocked(self):
        member = FakeMember(50)
        interaction = FakeInteraction(member, self.guild)
        with mock.patch("bot.cogs.moderation.is_staff", new=mock.AsyncMock(return_value=False)):
            await self.cog.scambait_warning_cmd.callback(self.cog, interaction)
        self.assertEqual(len(self.scambait_channel.sent), 0)
        self.assertEqual(interaction.response.messages[0]["content"], "Insufficient permissions.")


if __name__ == "__main__":
    unittest.main()
