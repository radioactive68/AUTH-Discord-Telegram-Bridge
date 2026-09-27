from django.db import models
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType


def create_dtb_permissions():
    """Create custom permissions for the DTB app."""
    # This is called during migration or setup
    pass


# Permission constants
PERM_ACCESS_DTB = 'aa_discord_telegram_bridge.access_dtb'
PERM_MANAGE_RULES = 'aa_discord_telegram_bridge.manage_dtb_rules'
PERM_VIEW_HISTORY = 'aa_discord_telegram_bridge.view_forward_history'
