"""Bearer-token authentication and permission enforcement for the GIS API.

Routes never inspect headers, tokens, or roles. They declare what a caller must be
allowed to DO:

    @router.get("/boundaries", dependencies=[Depends(require_permission(Permission.VIEW_GIS))])

and receive a `Principal` if they need to know who acted (the assessment log does). That
indirection is the whole point: swapping this for real OIDC/JWT means rewriting
`resolve_principal` and leaving every route untouched.

See app/config/auth_config.py for the security posture and its limits -- this is a seam,
not a full auth system.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import auth_config as config
from app.config.auth_config import Permission, Role

#: auto_error=False so this module controls the 401 body and WWW-Authenticate header,
#: rather than FastAPI emitting its own less specific message.
_bearer_scheme = HTTPBearer(auto_error=False, scheme_name="GIS bearer token")


def hash_token(token: str) -> str:
    """SHA-256 of a raw token. The registry only ever stores this."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Principal:
    """An authenticated caller and everything the API is allowed to know about them."""

    id: str
    display_name: str
    roles: tuple[str, ...]
    permissions: frozenset[str]

    def can(self, permission: Permission | str) -> bool:
        return (permission.value if isinstance(permission, Permission) else permission) in self.permissions


@dataclass(frozen=True)
class TokenRecord:
    """One issued token: its hash, who it represents, and what roles it carries."""

    token_hash: str
    id: str
    display_name: str
    roles: tuple[str, ...]

    def to_principal(self) -> Principal:
        return Principal(
            id=self.id,
            display_name=self.display_name,
            roles=self.roles,
            permissions=config.permissions_for_roles(self.roles),
        )


@dataclass
class TokenRegistry:
    """The set of tokens this deployment accepts.

    Held on `app.state` and built once at startup -- never re-read per request, so a
    filesystem hiccup cannot intermittently lock everyone out.
    """

    records: dict[str, TokenRecord] = field(default_factory=dict)
    source: str = "empty"

    def __len__(self) -> int:
        return len(self.records)

    def resolve(self, token: str) -> Principal | None:
        """Look up a raw token. Constant-time comparison, so a mismatch leaks no timing."""
        presented = hash_token(token)
        for stored_hash, record in self.records.items():
            if secrets.compare_digest(stored_hash, presented):
                return record.to_principal()
        return None

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, source: str) -> "TokenRegistry":
        records: dict[str, TokenRecord] = {}
        for index, entry in enumerate(payload.get("tokens") or []):
            token_hash = entry.get("token_hash")
            if not token_hash:
                raise ValueError(f"Token entry {index} has no token_hash (raw tokens are never stored).")
            roles = tuple(entry.get("roles") or ())
            unknown = set(roles) - set(config.ROLE_PERMISSIONS)
            if unknown:
                raise ValueError(f"Token entry {index} references unknown roles: {sorted(unknown)}")
            records[str(token_hash)] = TokenRecord(
                token_hash=str(token_hash),
                id=str(entry.get("id") or f"token-{index}"),
                display_name=str(entry.get("name") or entry.get("id") or f"token-{index}"),
                roles=roles,
            )
        return cls(records=records, source=source)

    @classmethod
    def for_testing(cls, tokens: Iterable[tuple[str, str, Iterable[str]]]) -> "TokenRegistry":
        """Build a registry from (raw_token, principal_id, roles) triples."""
        return cls.from_payload(
            {
                "tokens": [
                    {"token_hash": hash_token(raw), "id": principal_id, "name": principal_id, "roles": list(roles)}
                    for raw, principal_id, roles in tokens
                ]
            },
            source="in-memory",
        )


def build_token_registry(*, path: Path | None = None) -> TokenRegistry:
    """Load the registry from the environment, then the file, else return an empty one.

    An empty registry is not an error here -- it becomes a 503 at request time, with a
    message telling the operator how to fix it. Failing at startup instead would take the
    whole API down over a subsystem's misconfiguration.
    """
    inline = os.environ.get(config.TOKEN_REGISTRY_ENV_VAR)
    if inline:
        return TokenRegistry.from_payload(json.loads(inline), source=f"${config.TOKEN_REGISTRY_ENV_VAR}")

    path = path or config.TOKEN_REGISTRY_PATH
    if path.exists():
        return TokenRegistry.from_payload(json.loads(path.read_text(encoding="utf-8")), source=str(path))
    return TokenRegistry(source="empty")


# ---------------------------------------------------------------------------------
# Request-time resolution
# ---------------------------------------------------------------------------------


def _anonymous_principal() -> Principal:
    """The principal granted in explicitly-disabled auth mode (development only)."""
    return Principal(
        id=config.DISABLED_MODE_PRINCIPAL_ID,
        display_name="Anonymous (auth disabled)",
        roles=config.DISABLED_MODE_ROLES,
        permissions=config.permissions_for_roles(config.DISABLED_MODE_ROLES),
    )


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": config.AUTH_SCHEME},
    )


def resolve_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> Principal:
    """Identify the caller, or raise 401/503.

    This is the ONLY function that knows how callers are authenticated. Replace its body
    to adopt a real identity provider; every route keeps working unchanged.
    """
    if _auth_mode(request) == config.AUTH_MODE_DISABLED:
        return _anonymous_principal()

    registry: TokenRegistry | None = getattr(request.app.state, "token_registry", None)
    if registry is None or len(registry) == 0:
        # Fail closed, and say precisely how to fix it rather than just refusing.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "GIS API authentication is not configured. Generate a token registry with "
                "`python scripts/generate_api_tokens.py`, or set GIS_AUTH_MODE=disabled for "
                "local development only."
            ),
        )

    if credentials is None or not credentials.credentials:
        raise _unauthorized(f"Missing bearer token. Send an `Authorization: {config.AUTH_SCHEME} <token>` header.")
    if (credentials.scheme or "").lower() != config.AUTH_SCHEME.lower():
        raise _unauthorized(f"Unsupported authorization scheme {credentials.scheme!r}; expected {config.AUTH_SCHEME}.")

    principal = registry.resolve(credentials.credentials)
    if principal is None:
        raise _unauthorized("Invalid or revoked bearer token.")
    return principal


def _auth_mode(request: Request) -> str:
    """The effective auth mode. Overridable on app.state so tests need no env mutation."""
    return getattr(request.app.state, "auth_mode", None) or config.AUTH_MODE


def require_permission(*permissions: Permission) -> Callable[..., Principal]:
    """Build a dependency requiring ALL of `permissions`, returning the caller.

    Requiring all (rather than any) keeps endpoint declarations readable: what the route
    lists is exactly what the caller must hold.
    """
    required = tuple(permission.value for permission in permissions)

    def dependency(principal: Principal = Depends(resolve_principal)) -> Principal:
        missing = [permission for permission in required if permission not in principal.permissions]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Permission denied. This endpoint requires {', '.join(required)}; "
                    f"your roles ({', '.join(principal.roles) or 'none'}) are missing {', '.join(missing)}."
                ),
            )
        return principal

    dependency.__doc__ = f"Requires permission(s): {', '.join(required)}."
    return dependency


__all__ = [
    "Permission",
    "Principal",
    "Role",
    "TokenRegistry",
    "build_token_registry",
    "hash_token",
    "require_permission",
    "resolve_principal",
]
