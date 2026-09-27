from django.db import models
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType


def create_dtb_permissions():
    """Create custom permissions for the DTB app."""
    # This is called during migration or setup
    pass


# Permission constants
#
# DTB owns its permissions and never reads permissions of other apps: an
# Alliance Auth admin grants these to their own groups/states (e.g. "member",
# "FC", "leadership") and DTB checks exactly these.
APP_LABEL = 'aa_discord_telegram_bridge'
PERM_ACCESS_DTB = 'aa_discord_telegram_bridge.access_dtb'
PERM_MANAGE_RULES = 'aa_discord_telegram_bridge.manage_dtb_rules'
PERM_VIEW_HISTORY = 'aa_discord_telegram_bridge.view_forward_history'


def user_holds_perm(user, perm):
    """Does this user hold one of DTB's own permissions?

    ``user.has_perm()`` is the right question to ask, because Alliance Auth
    resolves its own permission model through the configured authentication
    backends — on a state-only install that is ``StateBackend``, which derives
    the permissions of the states a user holds, so a grant made on a state
    (e.g. ``Member``) is visible without ever touching
    ``user.user_permissions`` or a Django group. Reading those tables directly
    would therefore report "no access" for legitimately granted members.

    What DTB does *not* do is guess: it asks for its **own** permission and
    nothing else. It never inspects permissions of other apps (e.g. Secure
    Groups), and it never treats admin rights as user access — see
    :func:`user_can_use_dtb`.

    The one implication of Django semantics is worth spelling out: an active
    Django superuser holds every permission implicitly, so the site owner sees
    the service (and cannot be revoked by the periodic validation) without
    being granted anything. That is deliberate — an admin who administers DTB
    should not be locked out of it, and locking them out would kick their own
    Telegram account out of the bridge.
    """
    if user is None or not getattr(user, 'is_active', True):
        return False
    return user.has_perm(perm)


def user_can_use_dtb(user):
    """Access gate: the DTB service tile, the DTB pages, linking, join requests.

    Alliance Auth does not enforce ``ServicesHook.access_perm`` for a service
    app — it only *hands* the permission state to the service hook, which has to
    check it. DTB checks **its own** ``access_dtb`` permission, and nothing
    else: DTB admin rights (``manage_dtb_rules``) open the admin pages and do
    not imply the service, so a DTB admin can administer the bridge without
    being a member of the bridge.

    The same check drives the tile on ``/services/``, every DTB page, the
    link/unlink flow and the Telegram join-request approval, and losing it is
    what revokes the Telegram linkage (see ``validate_all_telegram_users``).
    """
    return user_holds_perm(user, PERM_ACCESS_DTB)


def user_is_dtb_admin(user):
    """DTB admin pages: needs ``manage_dtb_rules``, same resolution as the gate."""
    return user_holds_perm(user, PERM_MANAGE_RULES)


def user_can_view_history(user):
    """Forwarding history: needs ``view_forward_history``."""
    return user_holds_perm(user, PERM_VIEW_HISTORY)
