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

# Alliance membership is tracked by the Secure Groups app: its smart group
# holds every alliance member and grants this permission ("Can access sec
# group requests screen."). DTB treats it as the "is an alliance member"
# signal, so no alliance_id / EVE data lookup is needed.
PERM_SECURE_GROUP = 'securegroups.access_sec_group'
