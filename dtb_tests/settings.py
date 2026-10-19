"""Standalone Django settings for running the DTB test suite in CI.

Not used in production — an Alliance Auth install brings its own settings.
From the repository root::

    python -m django check --settings=dtb_tests.settings
    python -m django makemigrations --check --dry-run --settings=dtb_tests.settings
    python -m django test dtb_tests --settings=dtb_tests.settings

Environment switches (CI matrix):

``DTB_TEST_DB=mariadb``       use MySQL/MariaDB instead of the default SQLite
``DTB_TEST_DB_NAME/USER/``    connection overrides (see DATABASES below)

The settings mirror the Alliance Auth project template (settings/base.py)
closely enough that ``allianceauth.urls`` — including its
``decorate_url_patterns`` pass over our app — behaves exactly as on a real
install. Two deliberate differences: ``dtb`` IS declared public (so the
webhook route and the nested-include fix are exercised), and all logging/
caching stays local to the test process.
"""
import os
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = 'dtb-ci-only-not-a-production-secret'
DEBUG = True
DISPLAY_DEBUG = True
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'allianceauth',  # must stay first (favicons in Django admin)
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'django_celery_beat',
    'solo',
    'django_bootstrap5',
    'sortedm2m',
    'esi',
    'allianceauth.framework',
    'allianceauth.admin_status',
    'allianceauth.authentication',
    'allianceauth.services',
    'allianceauth.eveonline',
    'allianceauth.groupmanagement',
    'allianceauth.notifications',
    'allianceauth.thirdparty.navhelper',
    'allianceauth.analytics',
    'allianceauth.menu',
    'allianceauth.theme',
    'allianceauth.theme.darkly',
    'allianceauth.theme.flatly',
    'allianceauth.theme.materia',
    'allianceauth.custom_css',
    'allianceauth.crontab',
    'sri',
    # The plugin under test, then the harness app (this package).
    'aa_discord_telegram_bridge',
    'dtb_tests',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'allianceauth.authentication.middleware.UserSettingsMiddleware',
    'allianceauth.middleware.DeviceDetectionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'allianceauth.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.i18n',
                'django.template.context_processors.media',
                'django.template.context_processors.static',
                'django.template.context_processors.tz',
                'allianceauth.context_processors.auth_settings',
            ],
        },
    },
]

AUTHENTICATION_BACKENDS = [
    'allianceauth.authentication.backends.StateBackend',
    'django.contrib.auth.backends.ModelBackend',
]

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
}
if os.environ.get('DTB_TEST_DB', 'sqlite') == 'mariadb':
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('DTB_TEST_DB_NAME', 'dtb_ci'),
        'USER': os.environ.get('DTB_TEST_DB_USER', 'root'),
        'PASSWORD': os.environ.get('DTB_TEST_DB_PASSWORD', ''),
        'HOST': os.environ.get('DTB_TEST_DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('DTB_TEST_DB_PORT', '3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
        'TEST': {'CHARSET': 'utf8mb4', 'COLLATION': 'utf8mb4_unicode_ci'},
    }

# Alliance Auth connects django_redis during app ``ready()`` (task
# statistics), so the default cache must be the real backend — CI provides a
# Redis service; on a live install DB 15 keeps us clear of broker/cache DBs.
# Tests that need isolation (and must never clear a shared Redis) force
# LocMem via override_settings.
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': os.environ.get(
            'DTB_TEST_REDIS_URL', 'redis://127.0.0.1:6379/15',
        ),
        'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'},
    },
}
SESSION_ENGINE = 'django.contrib.sessions.backends.db'

DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

# --- Alliance Auth expectations ------------------------------------------

SRI_ALGORITHM = 'sha512'
SITE_NAME = 'Alliance Auth'
SITE_URL = 'http://localhost'
DEFAULT_THEME = 'allianceauth.theme.flatly.auth_hooks.FlatlyThemeHook'
LOGIN_URL = 'auth_login_user'
LOGIN_REDIRECT_URL = 'authentication:dashboard'
LOGOUT_REDIRECT_URL = 'authentication:dashboard'
LOGIN_TOKEN_SCOPES = ['publicData']
ACCOUNT_ACTIVATION_DAYS = 1
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
EMAIL_TIMEOUT = 15
ESI_API_URL = 'https://esi.evetech.net/'
from django.contrib.messages.constants import ERROR as _MESSAGE_ERROR  # noqa: E402

MESSAGE_TAGS = {_MESSAGE_ERROR: 'danger error'}
LANGUAGE_MAPPING = {
    'DataTables': {
        'cs-cz': 'cs', 'de': 'de-DE', 'es': 'es-ES', 'fr-fr': 'fr-FR',
        'it-it': 'it-IT', 'ja': 'ja', 'ko-kr': 'ko', 'nl-nl': 'nl',
        'pl-pl': 'pl', 'ru': 'ru', 'uk': 'uk', 'zh-hans': 'zh-HANT',
    },
    'MomentJS': {
        'cs-cz': 'cs', 'de': 'de', 'es': 'es', 'fr-fr': 'fr',
        'it-it': 'it', 'ja': 'ja', 'ko-kr': 'ko', 'nl-nl': 'nl',
        'pl-pl': 'pl', 'ru': 'ru', 'uk': 'uk', 'zh-hans': 'zh-cn',
    },
}

LANGUAGE_CODE = 'en-us'
LANGUAGE_COOKIE_AGE = 1209600
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
# sri's template tags call staticfiles_storage.path() (which requires
# STATIC_ROOT) whenever DEBUG is off — which the test environment forces.
# The actual files are then found through the app-static finders, so no
# collectstatic step is needed.
STATIC_ROOT = os.path.join(tempfile.gettempdir(), 'dtb_test_static')
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

# --- DTB-specific ---------------------------------------------------------

# Exercise the webhook route and the nested-include fix exactly as on an
# install that opted into public views.
APPS_WITH_PUBLIC_VIEWS = ['dtb']

# Keep log files out of the checkout; the plugin must create this itself.
DTB_LOG_DIR = os.path.join(tempfile.gettempdir(), 'dtb_test_logs')
DTB_LOG_LEVEL = 'DEBUG'

# Any real (unmocked) task enqueue stays in-process — no broker in CI.
CELERY_BROKER_URL = 'memory://'
CELERY_TASK_ALWAYS_EAGER = False
# AA's system checks (B003/B004) insist on these exact values.
CELERY_BROKER_TRANSPORT_OPTIONS = {
    'priority_steps': list(range(10)),
    'queue_order_strategy': 'priority',
}
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# AA/ESI checks (B007/B010, esi.E001/E003) require these to exist.
# The test environment forces DEBUG=False, which turns missing ESI SSO
# settings from a warning into an error — dummies satisfy both paths.
CSRF_TRUSTED_ORIGINS = ['http://localhost']
ESI_USER_CONTACT_EMAIL = 'dtb-ci@example.invalid'
ESI_SSO_CLIENT_ID = 'dtb-ci-dummy'
ESI_SSO_CLIENT_SECRET = 'dtb-ci-dummy'
ESI_SSO_CALLBACK_URL = 'http://localhost/sso/callback'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {'format': '%(levelname)s %(name)s %(message)s'},
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'loggers': {
        'django': {'handlers': ['console'], 'level': 'WARNING'},
        'allianceauth': {'handlers': ['console'], 'level': 'INFO'},
        'extensions': {'handlers': ['console'], 'level': 'INFO'},
    },
}

# Celery app (with celery_once ONCE config) must be current before any app
# imports our task modules — mirrors myauth/celery.py of a real install.
# Deliberately last: importing it reads django settings, and by here the
# module is complete even for that re-entrant access.
try:  # pragma: no cover - celery is present whenever this suite runs
    from dtb_tests.celery import app as celery_app  # noqa: E402, F401
except Exception:  # pragma: no cover
    celery_app = None
