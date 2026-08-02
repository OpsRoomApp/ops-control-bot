"""
v0.25.56 -- Rules cog tests.

Covers:
  * default rules returned when none stored
  * owner-only gating for rules-set / rules-reset
  * custom rules persist in guild_settings and are returned by /rules
  * reset removes the stored rules
  * rules are stored per guild
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

os.environ["DATABASE_PATH"] = os.path.join(tempfile.gettempdir(), "ops_control_rules_test.db")
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GUILD_ID", "1")
os.environ["OWNER_USER_ID"] = "2"
os.environ.setdefault("ARRIVALS_CHANNEL_ID", "3")

from bot.cogs.rules import DEFAULT_RULES, RulesCog  # noqa: E402
from bot.database import close_db, get_db, init_db  # noqa: E402


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.name = f"user{user_id}"
        self.display_name = f"User {user_id}"


class FakeResponse:
    def __init__(self):
        self.messages = []
        self.deferred = False

    async def send_message(self, content=None, ephemeral=False, **kwargs):
        self.messages.append({"content": content, "ephemeral": ephemeral, "embed": kwargs.get("embed")})

    async def defer(self, ephemeral=False, **kwargs):
        self.deferred = True

    def is_done(self):
        return self.deferred or bool(self.messages)


class FakeFollowup:
    def __init__(self, response: FakeResponse):
        self._response = response

    async def send(self, content=None, ephemeral=False, **kwargs):
        self._response.messages.append({"content": content, "ephemeral": ephemeral})


class FakeInteraction:
    def __init__(self, user_id: int, guild_id: int = 1, channel_id: int = 10):
        self.user = FakeUser(user_id)
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.response = FakeResponse()
        self.followup = FakeFollowup(self.response)


class RulesCogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        db = await get_db()
        await db.execute("DELETE FROM guild_settings WHERE key = 'rules'")
        await db.commit()
        self.cog = RulesCog(None)

    async def asyncTearDown(self):
        # Close the loop-bound connection so the next test's loop gets a
        # fresh one (aiosqlite connections are tied to the creating loop).
        await close_db()

    async def test_default_rules_when_none_stored(self):
        content = await self.cog._load_rules(1)
        self.assertEqual(content, DEFAULT_RULES)

    async def test_owner_can_set_rules(self):
        inter = FakeInteraction(user_id=2)  # owner
        await self.cog.rules_set.callback(self.cog, inter, "Rule one\nRule two")
        content = await self.cog._load_rules(1)
        self.assertEqual(content, "Rule one\nRule two")

    async def test_non_owner_cannot_set_rules(self):
        inter = FakeInteraction(user_id=1)  # not owner
        await self.cog.rules_set.callback(self.cog, inter, "sneaky")
        content = await self.cog._load_rules(1)
        self.assertEqual(content, DEFAULT_RULES)
        self.assertIn("restricted", inter.response.messages[0]["content"])

    async def test_non_owner_cannot_reset(self):
        await self.cog.rules_set.callback(self.cog, FakeInteraction(user_id=2), "custom")
        inter = FakeInteraction(user_id=1)
        await self.cog.rules_reset.callback(self.cog, inter)
        content = await self.cog._load_rules(1)
        self.assertEqual(content, "custom")

    async def test_owner_reset_restores_default(self):
        await self.cog.rules_set.callback(self.cog, FakeInteraction(user_id=2), "custom")
        inter = FakeInteraction(user_id=2)
        await self.cog.rules_reset.callback(self.cog, inter)
        content = await self.cog._load_rules(1)
        self.assertEqual(content, DEFAULT_RULES)

    async def test_rules_are_per_guild(self):
        await self.cog.rules_set.callback(self.cog, FakeInteraction(user_id=2, guild_id=1), "guild one")
        await self.cog.rules_set.callback(self.cog, FakeInteraction(user_id=2, guild_id=2), "guild two")
        self.assertEqual(await self.cog._load_rules(1), "guild one")
        self.assertEqual(await self.cog._load_rules(2), "guild two")


if __name__ == "__main__":
    unittest.main()
