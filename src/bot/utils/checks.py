"""
OPS CONTROL - Permission Checks (deprecated alias)

For new code, use `bot.utils.permissions` instead.
This module is kept for backward compatibility.
"""

from bot.utils.permissions import require_owner, require_owner_or_admin

__all__ = ["require_owner", "require_owner_or_admin"]
