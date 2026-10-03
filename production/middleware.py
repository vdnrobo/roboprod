from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import AccountActivity


class AccountActivityMiddleware:
    session_key = "account_activity_last_touch"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False):
            now = timezone.now()
            should_touch = True
            last_touch = request.session.get(self.session_key)
            if last_touch:
                parsed = parse_datetime(last_touch)
                if parsed and parsed >= now - timedelta(seconds=settings.ACCOUNT_ACTIVITY_UPDATE_INTERVAL_SECONDS):
                    should_touch = False
            if should_touch:
                AccountActivity.touch_user(user, now=now)
                request.session[self.session_key] = now.isoformat()
        return self.get_response(request)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("Referrer-Policy", "same-origin")
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not request.path.startswith("/django-admin/"):
            response.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; "
                "img-src 'self' data: blob:; "
                "style-src 'self'; "
                "font-src 'self'; "
                "script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval'; "
                "worker-src 'self'; "
                "connect-src 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'; "
                "base-uri 'self'",
            )
        return response
