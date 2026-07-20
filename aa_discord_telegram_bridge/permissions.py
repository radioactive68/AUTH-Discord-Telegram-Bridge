from django.db import models
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType


def create_dtb_permissions():
    """Create custom permissions for the DTB app."""
    # This is called during migration or setup
    pass


# Permission constants
PERM_ACCESS_DTB = 'dtb.access_dtb'
PERM_MANAGE_RULES = 'dtb.manage_dtb_rules'
PERM_VIEW_HISTORY = 'dtb.view_forward_history'
