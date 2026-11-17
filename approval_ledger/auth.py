"""Resource-server boundary using an external OAuth token introspection service.

This is not an OAuth issuer, login flow, or a complete remote MCP authorization server.
"""
import time
import math
from .domain import Forbidden, Principal


class IntrospectionAuthorizer:
    def __init__(self, introspect, audience, clock=time.time):
        self.introspect, self.audience, self.clock = introspect, audience, clock

    def authenticate(self, token):
        if not token or len(token) > 8192:
            raise Forbidden("invalid bearer token")
        try:
            claims = self.introspect(token)
        except Exception:
            raise Forbidden("token validation unavailable") from None
        if not isinstance(claims, dict):
            raise Forbidden("invalid introspection response")
        audience = claims.get("aud", [])
        if isinstance(audience, str):
            audience = [audience]
        expiration = claims.get("exp")
        if (claims.get("active") is not True or not isinstance(audience, list)
                or self.audience not in audience or type(expiration) not in (float, int)
                or not math.isfinite(expiration) or not self.clock() < expiration):
            raise Forbidden("inactive, expired or wrong-audience token")
        if any(not isinstance(claims.get(k), str) or not claims[k] for k in ("sub", "tenant", "scope")):
            raise Forbidden("missing principal claims")
        return Principal(claims["sub"], claims["tenant"], frozenset(claims["scope"].split()))
