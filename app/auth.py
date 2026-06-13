import hmac
import logging
import os
from fastapi import Header, HTTPException, status

logger = logging.getLogger(__name__)

# Environments where running without a token is tolerated (local convenience).
# Any other value (production, staging, …) is treated as a deployment that must
# never expose unauthenticated calc endpoints.
_INSECURE_ENVS = {"development", "local", "test"}


def _environment() -> str:
    return os.environ.get("ENVIRONMENT", "development")


def _token_required() -> bool:
    """True when a missing token must fail closed (non-dev deployments)."""
    return _environment() not in _INSECURE_ENVS


# Resolved once at import. ENVIRONMENT does not change within a running process,
# and caching it prevents a runtime os.environ mutation from silently flipping
# authentication off mid-process.
_TOKEN_REQUIRED: bool = _token_required()


# Fail fast at import (app startup) rather than on the first request: a non-dev
# deployment with no token is a misconfiguration, not a runtime condition.
if _TOKEN_REQUIRED and not os.environ.get("CALC_SERVICE_TOKEN", ""):
    raise RuntimeError(
        f"CALC_SERVICE_TOKEN is unset in ENVIRONMENT={_environment()!r}. "
        "The calc-service refuses to start unauthenticated outside "
        f"{sorted(_INSECURE_ENVS)}. Set CALC_SERVICE_TOKEN."
    )


def require_token(
    x_calc_service_token: str = Header(default=""),
) -> None:
    """Validate the app-layer secret carried in ``X-Calc-Service-Token``.

    The token travels in its own header so that ``Authorization`` is reserved
    exclusively for the Google OIDC identity token: when Cloud Run IAM
    authentication is enabled, that ``Authorization: Bearer <id_token>`` is
    validated at the platform edge before the request reaches the container.
    The two are separate transports — Cloud Run handles identity, this function
    handles the in-container app secret — so they must never share a header.
    """
    expected = os.environ.get("CALC_SERVICE_TOKEN", "")
    if not expected:
        # Only reachable in an insecure env (the import-time guard blocks the
        # non-dev case). Fail closed anyway if that ever changes.
        if _TOKEN_REQUIRED:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Calc service is misconfigured (no auth token).",
            )
        logger.warning(
            "CALC_SERVICE_TOKEN is not set — all endpoints are unprotected. "
            "Set CALC_SERVICE_TOKEN to enable authentication."
        )
        return

    if not hmac.compare_digest(x_calc_service_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing service token",
        )
