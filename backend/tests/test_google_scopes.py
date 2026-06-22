"""Google OAuth must request the Gmail *settings* scope.

Without ``gmail.settings.basic`` on the granted token, ``manage_gmail_filter``
(create/update Gmail filters) fails with a 403 "insufficient authentication
scopes" — the exact failure seen in log2.log. Listing it explicitly ensures the
consent screen surfaces it so a re-auth actually fixes the problem.
"""

from src.integrations.auth_providers import SUPPORTED_PROVIDERS

GMAIL_SETTINGS_SCOPE = "https://www.googleapis.com/auth/gmail.settings.basic"


def test_google_default_scopes_include_gmail_settings_basic():
    google = SUPPORTED_PROVIDERS["google"]
    assert GMAIL_SETTINGS_SCOPE in google.default_scopes
