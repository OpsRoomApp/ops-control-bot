"""
v0.25.55 (B4) -- Persistent reaction-role panel tests.

Covers:
  * stable custom_ids (what Discord uses to resolve handlers after a restart)
  * a freshly-constructed panel (simulating a bot restart) resolves every
    custom_id to a bound callback
  * role toggle works through the resolved callback (add + remove)
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GUILD_ID", "1")
os.environ.setdefault("OWNER_USER_ID", "2")
os.environ.setdefault("ARRIVALS_CHANNEL_ID", "3")

import discord  # noqa: E402

from bot.cogs.roles_cog import PersistentRolePanel  # noqa: E402

EXPECTED_CUSTOM_IDS = {
    "role:msfs2020",
    "role:msfs2024",
    "role:vatsim",
    "role:ivao",
    "role:publicbeta",
}


class _FakeRole:
    def __init__(self, name: str):
        self.id = name
        self.name = name


class _FakeMember:
    def __init__(self, roles: list):
        self.id = 123
        self.name = "user"
        self.display_name = "user"
        self.roles = roles
        self.added: list = []
        self.removed: list = []

    async def add_roles(self, role, reason=None):
        self.added.append(role.name)
        self.roles.append(role)

    async def remove_roles(self, role, reason=None):
        self.removed.append(role.name)
        if role in self.roles:
            self.roles.remove(role)


class _FakeGuild:
    def __init__(self, roles: list):
        self.id = 1
        self.name = "Test Guild"
        self.roles = roles


class _FakeResponse:
    def __init__(self):
        self.messages = []

    async def send_message(self, content=None, ephemeral=False, **kwargs):
        self.messages.append({"content": content, "ephemeral": ephemeral})


class RolePanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.roles = [_FakeRole("MSFS 2020"), _FakeRole("MSFS 2024"),
                      _FakeRole("VATSIM"), _FakeRole("IVAO"), _FakeRole("Public Beta")]

    def _fresh_panel(self) -> PersistentRolePanel:
        # A freshly constructed view == what Discord instantiates after restart.
        return PersistentRolePanel()

    def _interaction(self, member, guild):
        interaction = mock.Mock()
        interaction.user = mock.Mock(spec=discord.Member)
        interaction.user.id = member.id
        interaction.user.name = member.name
        interaction.user.display_name = member.display_name
        interaction.user.roles = member.roles
        interaction.user.add_roles = member.add_roles
        interaction.user.remove_roles = member.remove_roles
        interaction.guild = guild
        interaction.response = _FakeResponse()
        return interaction

    def test_custom_ids_stable_across_instances(self):
        """Every fresh panel exposes the same custom_ids (restart-safe contract)."""
        panel = self._fresh_panel()
        ids = {child.custom_id for child in panel.children}
        self.assertEqual(ids, EXPECTED_CUSTOM_IDS)

    def test_every_custom_id_resolves_to_callback(self):
        """Discord resolves persisted buttons by custom_id -> callback. After a
        simulated restart each child must still carry a bound handler."""
        panel = self._fresh_panel()
        for child in panel.children:
            self.assertIsNotNone(child.custom_id)
            self.assertTrue(callable(getattr(child, "callback", None)),
                            f"{child.custom_id} has no bound callback")

    async def test_role_toggle_through_resolved_callback(self):
        """Clicking 'VATSIM' through a fresh panel adds then removes the role."""
        member = _FakeMember(roles=[])
        guild = _FakeGuild(self.roles)
        panel = self._fresh_panel()

        vatsim_btn = next(c for c in panel.children if c.custom_id == "role:vatsim")

        interaction = self._interaction(member, guild)
        await vatsim_btn.callback(interaction)
        self.assertEqual(member.added, ["VATSIM"])
        self.assertEqual(interaction.response.messages[0]["content"], "Added VATSIM!")

        interaction2 = self._interaction(member, guild)
        await vatsim_btn.callback(interaction2)
        self.assertEqual(member.removed, ["VATSIM"])
        self.assertEqual(interaction2.response.messages[0]["content"], "Removed VATSIM.")


if __name__ == "__main__":
    unittest.main()
