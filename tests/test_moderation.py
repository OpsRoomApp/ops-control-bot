"""
v0.25.55 (B2) -- Moderation command + automod tests.

Covers:
  * permission check (non-staff blocked) for every command family
  * happy-path per command type (warn / kick / ban / timeout)
  * automod spam-threshold trigger
  * automod disabled rule produces no action
  * automod excessive-mentions trigger (timeout action)
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

os.environ["DATABASE_PATH"] = os.path.join(tempfile.gettempdir(), "ops_control_workorder_test.db")
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GUILD_ID", "1")
os.environ.setdefault("OWNER_USER_ID", "2")
os.environ.setdefault("ARRIVALS_CHANNEL_ID", "3")
os.environ.setdefault("PENDING_ACTION_MAX_ATTEMPTS", "3")
os.environ.setdefault("PENDING_ACTION_POLL_SECONDS", "2")

from bot.cogs.moderation import Moderation  # noqa: E402
from bot.database import get_db, init_db  # noqa: E402


class FakeResponse:
    def __init__(self):
        self.messages = []
        self.deferred = False

    async def send_message(self, content=None, ephemeral=False, **kwargs):
        self.messages.append({"content": content, "ephemeral": ephemeral})

    async def defer(self, ephemeral=False, **kwargs):
        self.deferred = True

    def is_done(self):
        return self.deferred or bool(self.messages)


class FakeFollowup:
    """Minimal stand-in for interaction.followup (records into a shared sink)."""

    def __init__(self, response: FakeResponse):
        self._response = response

    async def send(self, content=None, ephemeral=False, **kwargs):
        self._response.messages.append({"content": content, "ephemeral": ephemeral})


class FakeInteraction:
    def __init__(self, user, guild):
        self.user = user
        self.guild = guild
        self.guild_id = guild.id
        self.response = FakeResponse()
        self.followup = FakeFollowup(self.response)


class FakeGuild:
    def __init__(self, gid: int = 1):
        self.id = gid
        self.name = "Test Guild"

    def get_channel(self, channel_id):
        return None

    def get_member(self, user_id):
        return None


class FakeMember:
    def __init__(self, uid: int, name: str = "user", guild=None):
        self.id = uid
        self.name = name
        self.display_name = name
        self.guild = guild
        self.dms: list[str] = []
        self.kicked = False
        self.banned = False
        self.timeout_calls: list = []

    @property
    def mention(self):
        return f"<@{self.id}>"

    async def send(self, content=None, **kwargs):
        self.dms.append(content)
        return mock.Mock()

    async def kick(self, reason=None):
        self.kicked = True

    async def ban(self, reason=None, delete_message_days=0):
        self.banned = True
        self.ban_days = delete_message_days

    async def timeout(self, until=None, reason=None):
        self.timeout_calls.append(until)


class ModerationCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        db = await get_db()
        await db.execute("DELETE FROM moderation_cases")
        await db.execute("DELETE FROM automod_config")
        await db.commit()
        bot = mock.Mock()
        bot.user.id = 0
        self.cog = Moderation(bot)
        self.guild = FakeGuild(1)

    def _interaction(self, user):
        return FakeInteraction(user, self.guild)

    # -- Permission checks (non-staff blocked) -----------------------------

    async def test_non_staff_blocked_for_warn(self):
        member = FakeMember(50, "user50")
        interaction = self._interaction(member)
        with mock.patch("bot.cogs.moderation.is_staff", new=mock.AsyncMock(return_value=False)):
            await self.cog.warn_cmd.callback(self.cog, interaction, member, "spam")
        self.assertEqual(interaction.response.messages[0]["content"], "Insufficient permissions.")
        self.assertTrue(interaction.response.messages[0]["ephemeral"])
        self.assertEqual(member.dms, [])  # nothing sent to the target

    async def test_non_staff_blocked_for_kick_ban_timeout(self):
        member = FakeMember(51, "user51")
        interaction = self._interaction(member)
        with mock.patch("bot.cogs.moderation.is_staff", new=mock.AsyncMock(return_value=False)):
            await self.cog.kick_cmd.callback(self.cog, interaction, member, "x")
            await self.cog.ban_cmd.callback(self.cog, interaction, member, "x")
            await self.cog.timeout_cmd.callback(self.cog, interaction, member, 10, "x")
        self.assertFalse(member.kicked)
        self.assertFalse(member.banned)
        self.assertEqual(member.timeout_calls, [])
        for m in interaction.response.messages:
            self.assertEqual(m["content"], "Insufficient permissions.")

    # -- Happy paths -------------------------------------------------------

    async def test_warn_happy_path(self):
        member = FakeMember(52, "user52")
        interaction = self._interaction(member)
        with mock.patch("bot.cogs.moderation.is_staff", new=mock.AsyncMock(return_value=True)):
            await self.cog.warn_cmd.callback(self.cog, interaction, member, "spamming")
        self.assertEqual(member.dms, ["You have been warned in Test Guild: spamming"])
        self.assertIn("Warned", interaction.response.messages[0]["content"])
        db = await get_db()
        cur = await db.execute("SELECT action_type, reason FROM moderation_cases WHERE user_id=52")
        row = await cur.fetchone()
        self.assertEqual(row["action_type"], "WARN")
        self.assertEqual(row["reason"], "spamming")

    async def test_kick_happy_path(self):
        member = FakeMember(53, "user53")
        interaction = self._interaction(member)
        with mock.patch("bot.cogs.moderation.is_staff", new=mock.AsyncMock(return_value=True)):
            await self.cog.kick_cmd.callback(self.cog, interaction, member, "disruptive")
        self.assertTrue(member.kicked)
        self.assertIn("Kicked", interaction.response.messages[0]["content"])
        db = await get_db()
        cur = await db.execute("SELECT action_type FROM moderation_cases WHERE user_id=53")
        self.assertEqual((await cur.fetchone())["action_type"], "KICK")

    async def test_ban_happy_path(self):
        member = FakeMember(54, "user54")
        interaction = self._interaction(member)
        with mock.patch("bot.cogs.moderation.is_staff", new=mock.AsyncMock(return_value=True)):
            await self.cog.ban_cmd.callback(self.cog, interaction, member, "harassment", delete_message_days=3)
        self.assertTrue(member.banned)
        self.assertEqual(member.ban_days, 3)
        # Ban DM must point at the appeal form (C4 integration).
        self.assertTrue(any("appeal" in (d or "").lower() for d in member.dms))
        self.assertIn("Banned", interaction.response.messages[0]["content"])
        db = await get_db()
        cur = await db.execute("SELECT action_type FROM moderation_cases WHERE user_id=54")
        self.assertEqual((await cur.fetchone())["action_type"], "BAN")

    async def test_timeout_happy_path(self):
        member = FakeMember(55, "user55")
        interaction = self._interaction(member)
        with mock.patch("bot.cogs.moderation.is_staff", new=mock.AsyncMock(return_value=True)):
            await self.cog.timeout_cmd.callback(self.cog, interaction, member, 30, "spam")
        self.assertEqual(len(member.timeout_calls), 1)
        self.assertIn("Timed out", interaction.response.messages[0]["content"])
        db = await get_db()
        cur = await db.execute("SELECT action_type FROM moderation_cases WHERE user_id=55")
        self.assertEqual((await cur.fetchone())["action_type"], "TIMEOUT")


class AutomodTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        db = await get_db()
        await db.execute("DELETE FROM moderation_cases")
        await db.execute("DELETE FROM automod_config")
        await db.commit()
        bot = mock.Mock()
        bot.user.id = 0
        self.cog = Moderation(bot)
        self.guild = FakeGuild(1)

    def _message(self, uid: int):
        msg = mock.Mock()
        msg.author = FakeMember(uid, "user")
        msg.guild = self.guild
        msg.content = ""
        msg.mentions = []
        msg.role_mentions = []
        return msg

    async def test_spam_threshold_fires(self):
        """5 messages in the 5s window must trigger exactly one automod action."""
        msg = self._message(900)
        for _ in range(5):
            await self.cog._check_spam(msg)
        db = await get_db()
        cur = await db.execute("SELECT COUNT(*) AS c FROM moderation_cases WHERE action_type='AUTOMOD_WARN'")
        self.assertEqual((await cur.fetchone())["c"], 1)

    async def test_spam_below_threshold_no_action(self):
        msg = self._message(901)
        for _ in range(3):
            await self.cog._check_spam(msg)
        db = await get_db()
        cur = await db.execute("SELECT COUNT(*) AS c FROM moderation_cases WHERE user_id=901")
        self.assertEqual((await cur.fetchone())["c"], 0)

    async def test_disabled_rule_no_action(self):
        db = await get_db()
        await db.execute(
            "INSERT INTO automod_config (rule_key, enabled, action, threshold, updated_at) "
            "VALUES ('spam', 0, 'warn', 5, '2026-01-01')"
        )
        await db.commit()
        msg = self._message(902)
        for _ in range(6):
            await self.cog._check_spam(msg)
        cur = await db.execute("SELECT COUNT(*) AS c FROM moderation_cases WHERE user_id=902")
        self.assertEqual((await cur.fetchone())["c"], 0)

    async def test_excessive_mentions_triggers_timeout(self):
        msg = self._message(903)
        msg.mentions = [mock.Mock() for _ in range(8)]  # threshold default is 8
        await self.cog._check_mentions(msg)
        self.assertEqual(len(msg.author.timeout_calls), 1)


if __name__ == "__main__":
    unittest.main()
