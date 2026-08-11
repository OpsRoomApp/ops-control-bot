"""
Member verification gate tests.

Covers:
  * Verify button grants the member role and removes the unverified role
  * already-verified members are not double-granted
  * /verify-setup posts the persistent button message and stores it
  * /verify-setup is blocked for non-staff
  * the arrivals welcome message prompts the member to visit the verify channel
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

os.environ["DATABASE_PATH"] = os.path.join(tempfile.gettempdir(), "ops_control_verify_test.db")
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GUILD_ID", "1")
os.environ.setdefault("OWNER_USER_ID", "2")
os.environ.setdefault("ARRIVALS_CHANNEL_ID", "7777")

from bot.cogs.verify import Verify, VerifyView  # noqa: E402
from bot.cogs.welcome import WelcomeCog  # noqa: E402
from bot.database import get_db, init_db  # noqa: E402


def _verify_config():
    return mock.Mock(
        verify_channel_id=8888,
        verify_member_role_id=500,
        verify_unverified_role_id=501,
    )


class FakeRole:
    def __init__(self, rid: int, name: str):
        self.id = rid
        self.name = name


class FakeMember:
    bot = False

    def __init__(self, uid: int, name: str = "user", roles=None, guild=None):
        self.id = uid
        self.name = name
        self.display_name = name
        self.roles = roles or []
        self.guild = guild
        self.added: list = []
        self.removed: list = []

    @property
    def mention(self):
        return f"<@{self.id}>"

    async def add_roles(self, *roles, reason=None):
        self.added.extend(roles)
        for r in roles:
            if r not in self.roles:
                self.roles.append(r)

    async def remove_roles(self, *roles, reason=None):
        self.removed.extend(roles)
        for r in roles:
            if r in self.roles:
                self.roles.remove(r)


class FakeMessage:
    def __init__(self, mid: int):
        self.id = mid
        self.channel = mock.Mock(id=7777)


class FakeChannel:
    def __init__(self, cid: int, name: str):
        self.id = cid
        self.name = name
        self.sent: list = []

    async def send(self, content=None, embed=None, view=None, **kwargs):
        self.sent.append({"content": content, "embed": embed, "view": view})
        return FakeMessage(9001)

    async def fetch_message(self, message_id):
        raise Exception("not used")


class FakeGuild:
    def __init__(self, gid: int = 1, roles=None, channels=None):
        self.id = gid
        self.name = "Test Guild"
        self.roles = roles or {}
        self.channels = channels or {}

    def get_role(self, role_id):
        return self.roles.get(role_id)

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)


class FakeResponse:
    def __init__(self):
        self.messages: list = []
        self.deferred = False

    async def send_message(self, content=None, ephemeral=False, **kwargs):
        self.messages.append({"content": content, "ephemeral": ephemeral})

    async def defer(self, ephemeral=False, **kwargs):
        self.deferred = True


class FakeFollowup:
    def __init__(self, response):
        self._response = response

    async def send(self, content=None, ephemeral=False, **kwargs):
        self._response.messages.append({"content": content, "ephemeral": ephemeral})


class FakeInteraction:
    def __init__(self, user, guild, channel_id=8888):
        self.user = user
        self.guild = guild
        self.guild_id = guild.id
        self.channel_id = channel_id
        self.response = FakeResponse()
        self.followup = FakeFollowup(self.response)


class VerifyViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        db = await get_db()
        await db.execute("DELETE FROM moderation_cases")
        await db.execute("DELETE FROM guild_settings")
        await db.execute("DELETE FROM logs")
        await db.commit()
        self.member_role = FakeRole(500, "Member")
        self.unverified_role = FakeRole(501, "Unverified")
        self.guild = FakeGuild(
            1,
            roles={500: self.member_role, 501: self.unverified_role},
            channels={8888: FakeChannel(8888, "verify")},
        )
        self.view = VerifyView()
        self.cog = Verify(mock.Mock())
        self.config_patcher = mock.patch("bot.cogs.verify.config", new=_verify_config())
        self.config_patcher.start()
        self.addCleanup(self.config_patcher.stop)

    def _interaction(self, member):
        return FakeInteraction(member, self.guild)

    async def test_verify_button_grants_member_role(self):
        member = FakeMember(700)
        interaction = self._interaction(member)
        await self.view.children[0].callback(interaction)
        self.assertIn(self.member_role, member.roles)
        self.assertEqual(member.added, [self.member_role])
        self.assertIn("verified", interaction.response.messages[0]["content"].lower())
        self.assertTrue(interaction.response.messages[0]["ephemeral"])
        db = await get_db()
        cur = await db.execute(
            "SELECT event_type FROM logs WHERE user_id=700 ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        self.assertEqual(row["event_type"], "verify")

    async def test_verify_button_removes_unverified_role(self):
        member = FakeMember(701, roles=[self.unverified_role])
        interaction = self._interaction(member)
        await self.view.children[0].callback(interaction)
        self.assertIn(self.member_role, member.roles)
        self.assertNotIn(self.unverified_role, member.roles)
        self.assertEqual(member.removed, [self.unverified_role])

    async def test_verify_button_already_verified(self):
        member = FakeMember(702, roles=[self.member_role])
        interaction = self._interaction(member)
        await self.view.children[0].callback(interaction)
        self.assertEqual(member.added, [])
        self.assertIn("already verified", interaction.response.messages[0]["content"].lower())


class VerifySetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        db = await get_db()
        await db.execute("DELETE FROM guild_settings")
        await db.execute("DELETE FROM logs")
        await db.commit()
        self.channel = FakeChannel(8888, "verify")
        self.guild = FakeGuild(1, channels={8888: self.channel})
        self.cog = Verify(mock.Mock())
        self.config_patcher = mock.patch("bot.cogs.verify.config", new=_verify_config())
        self.config_patcher.start()
        self.addCleanup(self.config_patcher.stop)

    def _interaction(self, member):
        return FakeInteraction(member, self.guild)

    async def test_setup_posts_button_and_stores_message(self):
        staff = FakeMember(2)
        interaction = self._interaction(staff)
        with mock.patch("bot.cogs.verify.is_staff", new=mock.AsyncMock(return_value=True)),               mock.patch("discord.TextChannel", FakeChannel):
            await self.cog.verify_setup_cmd.callback(self.cog, interaction)
        self.assertEqual(len(self.channel.sent), 1)
        self.assertIsNotNone(self.channel.sent[0]["embed"])
        self.assertIsNotNone(self.channel.sent[0]["view"])
        self.assertIn("Verify button is live", interaction.response.messages[0]["content"])
        self.assertTrue(interaction.response.messages[0]["ephemeral"])
        db = await get_db()
        cur = await db.execute(
            "SELECT value FROM guild_settings WHERE guild_id=1 AND key='verify_message'"
        )
        row = await cur.fetchone()
        self.assertIsNotNone(row)
        self.assertIn("8888", row["value"])

    async def test_setup_non_staff_blocked(self):
        member = FakeMember(50)
        interaction = self._interaction(member)
        with mock.patch("bot.cogs.verify.is_staff", new=mock.AsyncMock(return_value=False)):
            await self.cog.verify_setup_cmd.callback(self.cog, interaction)
        self.assertEqual(len(self.channel.sent), 0)
        self.assertEqual(interaction.response.messages[0]["content"], "Insufficient permissions.")


class WelcomePromptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = mock.Mock()
        self.channel = FakeChannel(7777, "arrivals")
        self.bot.get_channel.return_value = self.channel
        self.cog = WelcomeCog(self.bot)

    async def test_welcome_message_prompts_verify_channel(self):
        guild = FakeGuild(1)
        member = FakeMember(800, guild=guild)
        welcome_config = mock.Mock(
            guild_id=1,
            arrivals_channel_id=7777,
            verify_channel_id=8888,
        )
        fake_db = mock.Mock()
        fake_db.execute = mock.AsyncMock()
        fake_db.commit = mock.AsyncMock()
        fd, img_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        self.addCleanup(os.unlink, img_path)
        fake_generator = mock.Mock(generate=lambda name: img_path)
        with mock.patch("bot.cogs.welcome.config", new=welcome_config),               mock.patch("bot.cogs.welcome.get_db", new=mock.AsyncMock(return_value=fake_db)),               mock.patch("bot.cogs.welcome.log_event", new=mock.AsyncMock()),               mock.patch.object(WelcomeCog, "generator", new=mock.PropertyMock(return_value=fake_generator)),               mock.patch("bot.cogs.welcome.log_member_join", new=mock.AsyncMock()),               mock.patch("discord.TextChannel", FakeChannel):
            await self.cog.on_member_join(member)
        self.assertEqual(len(self.channel.sent), 1)
        content = self.channel.sent[0]["content"]
        self.assertIn("<#8888>", content)
        self.assertIn("verify", content.lower())


if __name__ == "__main__":
    unittest.main()
