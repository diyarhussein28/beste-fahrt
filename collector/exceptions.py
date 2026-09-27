class RateLimited(Exception):
    """Platform returned 429/403 or an equivalent block — القسم 4.3."""


class LoginBlocked(Exception):
    """CAPTCHA or 2FA appeared. The collector must stop and alert a human
    instead of attempting to solve it — القسم 4.2: 'لا يتم تجاوز هذه الحماية آلياً.'
    """


class PlatformChanged(Exception):
    """Parsing failed in a way that suggests the platform's markup changed
    (e.g. zero offer cards found repeatedly) — القسم 9.3.
    """
