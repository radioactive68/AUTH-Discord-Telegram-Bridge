from django.db import models
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldError
from django.db.models import Q


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


def _codename(perm):
    """Accept both ``access_dtb`` and ``<app_label>.access_dtb``."""
    return perm.rsplit('.', 1)[-1]


def _perm_q(prefix, codename):
    """Q matching a DTB permission reached through the m2m ``prefix``."""
    return Q(**{
        prefix + '__content_type__app_label': APP_LABEL,
        prefix + '__codename': codename,
    })


def _state_grant_queries(codename):
    """Q objects covering every way an Alliance Auth state can carry a permission.

    A state grant lives in ``StateMembership``: the permissions and groups of
    the state are copied onto the membership, and the state itself keeps them in
    its own m2m fields. Which of those exist differs between AA versions, so the
    field names are read from the model instead of being hardcoded.

    Both are checked on purpose: a permission added to the state *after* the
    grant only exists in the state definition, and waiting for AA to sync it
    onto the user would make DTB access depend on sync timing.
    """
    try:
        from allianceauth.authentication.models import StateMembership
    except Exception:
        return []

    queries = []

    def add(prefix, target):
        if target is Permission:
            queries.append(_perm_q(prefix, codename))
        elif target is Group:
            queries.append(_perm_q(prefix + '__permissions', codename))

    state_fields = []
    for field in StateMembership._meta.get_fields():
        if getattr(field, 'many_to_many', False):
            add(field.name, field.related_model)
        elif field.name != 'user' and getattr(field, 'many_to_one', False):
            # the state itself: its own permissions / groups
            state_fields.append(field)
    for field in state_fields:
        meta = getattr(field.related_model, '_meta', None)
        if meta is None:
            continue
        for sub in meta.get_fields():
            if getattr(sub, 'many_to_many', False):
                add(field.name + '__' + sub.name, sub.related_model)
    return queries


def _user_holds_state_perm(user, codename):
    try:
        from allianceauth.authentication.models import StateMembership
    except Exception:
        return False

    queries = _state_grant_queries(codename)
    if not queries:
        return False
    combined = queries[0]
    for extra in queries[1:]:
        combined |= extra
    try:
        return StateMembership.objects.filter(user=user).filter(combined).exists()
    except FieldError:
        # AA version with other field names: try every source on its own
        for query in queries:
            try:
                if StateMembership.objects.filter(
                    user=user,
                ).filter(query).exists():
                    return True
            except FieldError:
                continue
        return False


def user_holds_perm(user, perm):
    """Explicitly check one of DTB's own permissions — no implicit access.

    ``user.has_perm()`` cannot be used for this:

    * Django answers ``True`` for *every* permission when the user is a superuser,
      so the access permission would be meaningless — a superuser would see the
      service, and keep Telegram access, without ever being granted it;
    * on a state-only AA install (no Auth groups) the permission lives in the
      state grant, and AA only copies it onto the user when it syncs states.

    So the permission is resolved from the actual grants instead:

    1. granted to the user directly (``user_permissions``);
    2. granted to one of the user's groups;
    3. carried by an Alliance Auth state granted to the user — both the grant
       record and the state definition.

    Being an alliance member, holding an AA role, or being a Django superuser
    grants nothing on its own.
    """
    codename = _codename(perm)
    if user is None or not getattr(user, 'is_active', True):
        return False
    try:
        if user.user_permissions.filter(
            content_type__app_label=APP_LABEL, codename=codename,
        ).exists():
            return True
        if user.groups.filter(
            permissions__content_type__app_label=APP_LABEL,
            permissions__codename=codename,
        ).exists():
            return True
    except FieldError:
        return False
    return _user_holds_state_perm(user, codename)


def user_can_use_dtb(user):
    """Access gate: the DTB service tile, the DTB pages, linking, join requests.

    Alliance Auth does not enforce ``ServicesHook.access_perm`` for a service
    app — it only *hands* the permission state to the service hook, which has to
    check it. DTB checks **its own** ``access_dtb`` permission, and nothing else:
    DTB admin rights (``manage_dtb_rules``) do not imply it, and neither does
    being a Django superuser. Grant the permission explicitly to everyone who
    should see the service — through a group or through a state.

    The same check drives the tile on ``/services/``, every DTB page, the
    link/unlink flow and the Telegram join-request approval, and losing it is
    what revokes the Telegram linkage (see ``validate_all_telegram_users``).
    """
    return user_holds_perm(user, PERM_ACCESS_DTB)


def user_is_dtb_admin(user):
    """DTB admin pages: needs ``manage_dtb_rules``, resolved like the gate.

    Same explicit resolution as :func:`user_can_use_dtb`, so a DTB admin who
    was granted the permission through a state is recognised as well.
    """
    return user_holds_perm(user, PERM_MANAGE_RULES)


def user_can_view_history(user):
    """Forwarding history: needs ``view_forward_history``."""
    return user_holds_perm(user, PERM_VIEW_HISTORY)
