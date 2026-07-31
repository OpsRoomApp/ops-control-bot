"""
OPS CONTROL - Ticket System

Complete Discord ticket system:
- Persistent support panel with buttons
- Modal-based ticket creation (support tickets and bug reports)
- Private ticket channels with role-based permissions
- Claim Ticket + Close Ticket buttons
- Database records for all tickets and bugs
- Bug report notifications to the bug reports channel
"""

from __future__ import annotations

import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.database import get_db
from bot.utils.helpers import utc_now_iso
from bot.services.audit import log_event
from bot.services.discord_log import (
    log_ticket_created,
    log_ticket_closed,
    log_bug_submitted,
)

logger = logging.getLogger("ops_control.cogs.tickets")

# Role IDs (from config)
SUPPORT_DISPATCH = config.support_dispatch_role_id
MODERATOR_ROLE = config.moderator_role_id
OPS_CONTROL_ROLE = config.ops_control_role_id
BUG_REPORTS_CHANNEL = config.bug_reports_channel_id

# Category ID for ticket channels
TICKET_CATEGORY_ID = config.support_category_id

# Ticket counter lives next to the database (Docker-safe, no relative path assumptions)
TICKET_COUNTER_FILE = str(Path(config.database_path).parent / "ticket_counter.txt")


def _get_next_ticket_number() -> int:
    """Return the next ticket number and persist it."""
    counter_path = Path(TICKET_COUNTER_FILE)
    try:
        with open(counter_path, "r") as f:
            num = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        num = 0

    num += 1
    try:
        counter_path.parent.mkdir(parents=True, exist_ok=True)
        with open(counter_path, "w") as f:
            f.write(str(num))
    except OSError:
        pass

    return num


def _is_staff(member: discord.Member) -> bool:
    """Return True if the member has a staff role or is the owner."""
    if member.id == config.owner_user_id:
        return True
    if member.guild_permissions.administrator:
        return True
    staff_roles = (SUPPORT_DISPATCH, MODERATOR_ROLE, OPS_CONTROL_ROLE)
    return any(rid and any(r.id == rid for r in member.roles) for rid in staff_roles)


# ---------------------------------------------------------------------------
# Support Ticket Modal
# ---------------------------------------------------------------------------


class SupportTicketModal(discord.ui.Modal, title="Create Support Ticket"):
    """Modal form for creating a support ticket."""

    subject = discord.ui.TextInput(
        label="Subject",
        placeholder="Brief description of your issue",
        required=True,
        max_length=100,
    )
    description = discord.ui.TextInput(
        label="Description",
        placeholder="Describe your issue in detail. Include relevant information.",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )
    priority = discord.ui.TextInput(
        label="Priority",
        placeholder="Low, Normal, High, or Critical",
        required=False,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        subject = self.subject.value.strip()
        description = self.description.value.strip()
        priority = (self.priority.value.strip() or "Normal")[:20]
        ticket_num = _get_next_ticket_number()

        # Create private ticket channel
        channel = await _create_ticket_channel(
            interaction,
            ticket_num,
            subject,
            "support",
        )

        channel_mention = channel.mention if channel else "N/A"

        if channel:
            embed = discord.Embed(
                title=f"Support Ticket #{ticket_num}",
                color=0x8B5CF6,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Created by", value=f"{interaction.user.mention} ({interaction.user.display_name})", inline=False)
            embed.add_field(name="Priority", value=priority, inline=True)
            embed.add_field(name="Subject", value=subject, inline=False)
            embed.add_field(name="Description", value=description[:1024], inline=False)
            embed.set_footer(text="A staff member will assist you shortly.")

            view = TicketActionView(ticket_num)
            await channel.send(
                content=f"{interaction.user.mention} Support Dispatch - please assist.",
                embed=embed,
                view=view,
            )

        # Save to database
        db = await get_db()
        cursor = await db.execute(
            """
            INSERT INTO tickets (user_id, username, category, priority, description, subject, channel_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (interaction.user.id, interaction.user.display_name, "support", priority, description, subject, channel.id if channel else None, utc_now_iso()),
        )
        await db.commit()

        await interaction.followup.send(
            f"Support ticket **#{ticket_num}** created. {channel_mention}",
            ephemeral=True,
        )

        await log_event(
            "ticket",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=interaction.guild_id,  # type: ignore[arg-type]
            channel_id=channel.id if channel else None,
            detail=f"Support ticket #{ticket_num}: {subject} ({priority})",
        )

        if isinstance(interaction.user, discord.Member):
            await log_ticket_created(
                interaction.client,  # type: ignore[arg-type]
                interaction.user,
                ticket_num,
                subject,
                channel_mention,
            )


# ---------------------------------------------------------------------------
# Bug Report Modal
# ---------------------------------------------------------------------------


class BugReportModal(discord.ui.Modal, title="Report a Bug"):
    """Modal form for structured bug reports.

    Discord limits modals to 5 TextInput fields.
    Fields: Title, Version, Module, Description, Steps to reproduce.
    """

    title_input = discord.ui.TextInput(
        label="Bug Title",
        placeholder="Brief summary of the issue",
        required=True,
        max_length=100,
    )
    version = discord.ui.TextInput(
        label="OPS ROOM Version",
        placeholder="e.g. v0.24.106",
        required=True,
        max_length=50,
    )
    module = discord.ui.TextInput(
        label="Module / Area",
        placeholder="e.g. Black Box, Flight Planner, Camera Bridge",
        required=True,
        max_length=100,
    )
    description = discord.ui.TextInput(
        label="Description",
        placeholder="What happened? Include simulator and aircraft if relevant.",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )
    steps = discord.ui.TextInput(
        label="Steps to Reproduce",
        placeholder="Step-by-step instructions to reproduce the issue.",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        title = self.title_input.value.strip()
        version = self.version.value.strip()
        module = self.module.value.strip()
        description = self.description.value.strip()
        steps_text = self.steps.value.strip() or None

        ticket_num = _get_next_ticket_number()

        # Create private ticket channel
        channel = await _create_ticket_channel(
            interaction,
            ticket_num,
            title,
            "bug",
        )

        channel_mention = channel.mention if channel else "N/A"

        if channel:
            embed = discord.Embed(
                title=f"Bug Report #{ticket_num}",
                color=0xF59E0B,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Reporter", value=f"{interaction.user.mention} ({interaction.user.display_name})", inline=False)
            embed.add_field(name="Version", value=version, inline=True)
            embed.add_field(name="Module", value=module, inline=True)
            embed.add_field(name="Title", value=title, inline=False)
            embed.add_field(name="Description", value=description[:1024], inline=False)
            if steps_text:
                embed.add_field(name="Steps to Reproduce", value=steps_text[:1024], inline=False)
            embed.set_footer(text="A staff member will review this report.")

            view = TicketActionView(ticket_num)
            await channel.send(
                content=f"{interaction.user.mention} Support Dispatch - bug report.",
                embed=embed,
                view=view,
            )

        # Save to database
        db = await get_db()
        cursor = await db.execute(
            """
            INSERT INTO bugs (reporter_id, reporter_name, version, module, description, steps, title, channel_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (interaction.user.id, interaction.user.display_name, version, module, description, steps_text, title, channel.id if channel else None, utc_now_iso()),
        )
        await db.commit()
        bug_db_id = cursor.lastrowid

        # Notify bug reports channel
        bug_channel_id = BUG_REPORTS_CHANNEL
        if bug_channel_id and interaction.guild:
            bug_ch = interaction.guild.get_channel(bug_channel_id)
            if bug_ch and isinstance(bug_ch, discord.TextChannel):
                notify_embed = discord.Embed(
                    title="New Bug Report",
                    color=0xF59E0B,
                    timestamp=discord.utils.utcnow(),
                )
                notify_embed.add_field(name="Reporter", value=interaction.user.mention, inline=True)
                notify_embed.add_field(name="Ticket", value=f"#{ticket_num}", inline=True)
                notify_embed.add_field(name="Title", value=title, inline=False)
                notify_embed.add_field(name="Description", value=description[:1024], inline=False)
                notify_embed.add_field(name="Version", value=version, inline=True)
                notify_embed.add_field(name="Module", value=module, inline=True)
                notify_embed.set_footer(text=f"Bug ID: {bug_db_id}")
                try:
                    await bug_ch.send(
                        embed=notify_embed,
                        view=EscalateToSupportView(ticket_num),
                    )
                except Exception:
                    logger.exception("Failed to notify bug reports channel")

        await interaction.followup.send(
            f"Bug report **#{ticket_num}** submitted. {channel_mention}",
            ephemeral=True,
        )

        await log_event(
            "bug",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=interaction.guild_id,  # type: ignore[arg-type]
            channel_id=channel.id if channel else None,
            detail=f"Bug #{ticket_num}: {title}",
        )

        if isinstance(interaction.user, discord.Member):
            await log_bug_submitted(
                interaction.client,  # type: ignore[arg-type]
                interaction.user,
                ticket_num,
                title,
                channel_mention,
            )


# ---------------------------------------------------------------------------
# Escalate-to-support view (attached to bug report notifications)
# ---------------------------------------------------------------------------


class EscalateToSupportView(discord.ui.View):
    """Button to create a support ticket from a bug report notification."""

    def __init__(self, bug_num: int = 0) -> None:
        super().__init__(timeout=None)
        self.bug_num = bug_num

    @discord.ui.button(
        label="Create Support Ticket",
        style=discord.ButtonStyle.primary,
        custom_id="bug:escalate_support",
    )
    async def escalate(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Open the support ticket modal for escalation."""
        await interaction.response.send_modal(SupportTicketModal())


# ---------------------------------------------------------------------------
# Ticket Action View (Claim + Close)
# ---------------------------------------------------------------------------


class TicketActionView(discord.ui.View):
    """View with claim and close buttons for ticket channels."""

    def __init__(self, ticket_num: int = 0) -> None:
        super().__init__(timeout=None)
        self._ticket_num = ticket_num

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.success, custom_id="ticket:claim")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Claim the ticket (staff only)."""
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.defer(ephemeral=True)
            return

        if not _is_staff(interaction.user):
            await interaction.response.send_message(
                "You do not have permission to claim tickets.",
                ephemeral=True,
            )
            return

        # Update claim in database and channel
        ch_id = interaction.channel_id
        db = await get_db()
        await db.execute(
            "UPDATE tickets SET assigned_to = ?, updated_at = ? WHERE channel_id = ?",
            (interaction.user.id, utc_now_iso(), ch_id),
        )
        await db.execute(
            "UPDATE bugs SET assigned_to = ?, updated_at = ? WHERE channel_id = ?",
            (interaction.user.id, utc_now_iso(), ch_id),
        )
        await db.commit()

        await interaction.response.send_message(
            f"Ticket claimed by {interaction.user.mention}.",
            ephemeral=False,
        )

        await log_event(
            "ticket_claimed",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=interaction.guild_id,
            channel_id=ch_id,
            detail=f"Ticket #{self._ticket_num} claimed by {interaction.user.display_name}",
        )

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket:close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Close the ticket channel (staff only)."""
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.defer(ephemeral=True)
            return

        if not _is_staff(interaction.user):
            await interaction.response.send_message(
                "You do not have permission to close this ticket.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("Closing ticket...", ephemeral=True)

        # Resolve ticket info from database
        ch_id = interaction.channel_id
        db = await get_db()
        cursor = await db.execute(
            "SELECT id, user_id, username, subject FROM tickets WHERE channel_id = ?",
            (ch_id,),
        )
        ticket_row = await cursor.fetchone()

        if not ticket_row:
            cursor = await db.execute(
                "SELECT id, reporter_id AS user_id, reporter_name AS username, title AS subject FROM bugs WHERE channel_id = ?",
                (ch_id,),
            )
            ticket_row = await cursor.fetchone()

        t_id: int = ticket_row["id"] if ticket_row else 0
        t_subject = ticket_row["subject"] if ticket_row else "unknown"
        t_creator = ticket_row["username"] if ticket_row else "unknown"

        await db.execute(
            "UPDATE tickets SET status = 'closed', updated_at = ? WHERE channel_id = ?",
            (utc_now_iso(), ch_id),
        )
        await db.execute(
            "UPDATE bugs SET status = 'closed', updated_at = ? WHERE channel_id = ?",
            (utc_now_iso(), ch_id),
        )
        await db.commit()

        await log_event(
            "ticket_closed",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=interaction.guild_id,
            channel_id=ch_id,
            detail=f"Ticket #{t_id} closed: {t_subject}",
        )

        if isinstance(interaction.user, discord.Member):
            await log_ticket_closed(
                interaction.client,  # type: ignore[arg-type]
                interaction.user,
                t_id,
                t_creator,
            )

        try:
            await interaction.channel.delete()  # type: ignore[union-attr]
        except Exception:
            logger.exception("Failed to delete ticket channel")


# ---------------------------------------------------------------------------
# Support Panel View (persistent buttons)
# ---------------------------------------------------------------------------


class SupportPanelView(discord.ui.View):
    """Persistent view with support ticket and bug report buttons."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Support Ticket",
        style=discord.ButtonStyle.primary,
        custom_id="support:create_ticket",
    )
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Open the support ticket modal."""
        await interaction.response.send_modal(SupportTicketModal())

    @discord.ui.button(
        label="Report Bug",
        style=discord.ButtonStyle.danger,
        custom_id="support:report_bug",
    )
    async def report_bug(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Open the bug report modal."""
        await interaction.response.send_modal(BugReportModal())


# ---------------------------------------------------------------------------
# Channel Creation Helper
# ---------------------------------------------------------------------------


async def _create_ticket_channel(
    interaction: discord.Interaction,
    ticket_num: int,
    subject: str,
    ticket_type: str,
) -> discord.TextChannel | None:
    """Create a private ticket channel with proper permissions.

    Returns the created channel or None on failure.
    """
    if not interaction.guild:
        return None

    # Sanitize channel name
    safe_subject = "".join(c for c in subject if c.isalnum() or c in " _-")[:30]
    channel_name = f"ticket-{ticket_num}-{safe_subject.lower().replace(' ', '-')}"

    category = None
    if TICKET_CATEGORY_ID:
        category = interaction.guild.get_channel(TICKET_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            category = None

    # Build permission overwrites
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }

    # Add support roles
    for role_id in (SUPPORT_DISPATCH, MODERATOR_ROLE, OPS_CONTROL_ROLE):
        if role_id:
            role = interaction.guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    try:
        channel = await interaction.guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"{ticket_type} ticket #{ticket_num} by {interaction.user.display_name}",
        )
        logger.info("Created ticket channel: %s", channel.name)
        return channel
    except Exception:
        logger.exception("Failed to create ticket channel")
        return None


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class TicketSystemCog(commands.Cog):
    """Complete ticket system with support panel, modals, and private channels."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="setup-support-panel",
        description="Create the persistent support panel in this channel.",
    )
    @app_commands.default_permissions(administrator=True)
    async def setup_support_panel(self, interaction: discord.Interaction) -> None:
        """Create a persistent support panel with buttons."""
        if not isinstance(interaction.user, discord.Member) or not _is_staff(interaction.user):
            await interaction.response.send_message(
                "You need Administrator permissions to set up the support panel.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="OPS ROOM Support",
            description=(
                "Need assistance? Create a ticket and our team will help you.\n\n"
                "**Create Support Ticket** - General help, installation, account, or technical issues.\n"
                "**Report Bug** - Report a problem with OPS ROOM software."
            ),
            color=0x2563EB,
        )
        embed.set_footer(text="OPS ROOM Operations Platform")

        view = SupportPanelView()
        await interaction.channel.send(embed=embed, view=view)  # type: ignore[union-attr]

        await interaction.response.send_message(
            "Support panel created in this channel.",
            ephemeral=True,
        )

    @app_commands.command(
        name="support",
        description="Create a support ticket.",
    )
    async def support(self, interaction: discord.Interaction) -> None:
        """Open the support ticket modal."""
        await interaction.response.send_modal(SupportTicketModal())

    @app_commands.command(
        name="bug",
        description="Report a bug in OPS ROOM.",
    )
    async def bug(self, interaction: discord.Interaction) -> None:
        """Open the bug report modal."""
        await interaction.response.send_modal(BugReportModal())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketSystemCog(bot))
    logger.info("Ticket system cog loaded.")
