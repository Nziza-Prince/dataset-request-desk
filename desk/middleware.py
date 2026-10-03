import json
import logging
import time

logger = logging.getLogger("desk.requests")


class RequestLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        response = self.get_response(request)
        user = getattr(request, "user", None)
        logger.info(json.dumps({
            "method": request.method, "path": request.path,
            "status": response.status_code,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            "user_id": user.pk if user and user.is_authenticated else None,
        }))
        response["Cache-Control"] = "no-store"
        response["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        )
        return response
