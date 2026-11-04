from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework.authtoken.models import Token

from apps.core.utils.user_roles import is_client_user


@database_sync_to_async
def _user_from_token(token: str | None):
    if not token:
        return AnonymousUser()
    try:
        auth_token = Token.objects.select_related("user").get(key=token)
    except Token.DoesNotExist:
        return AnonymousUser()
    user = auth_token.user
    if not user.is_active or is_client_user(user):
        return AnonymousUser()
    return user


class TokenAuthMiddleware(BaseMiddleware):
    """Autenticación WS vía ?token= (DRF Token, mismo que el front)."""

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "websocket":
            raw = scope.get("query_string", b"")
            query = parse_qs(raw.decode())
            token = query.get("token", [None])[0]
            scope["user"] = await _user_from_token(token)
        return await super().__call__(scope, receive, send)


def TokenAuthMiddlewareStack(inner):
    return TokenAuthMiddleware(inner)
