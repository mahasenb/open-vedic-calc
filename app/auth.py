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
    # Default to 'production' so that a missing ENVIRONMENT variable is
    # fail-secure: auth is ON unless the operator explicitly sets ENVIRONMENT
    # to a known insecure value (development / local / test).
    # The old default 'development' was fail-open — a misconfigured or newly
    # deployed container with no ENVIRONMENT set would run with auth disabled.
    return os.environ.get("ENVIRONMENT", "production")


def _token_required() -> bool:
    """True when a missing token must fail closed (non-dev deployments)."""
    return _environment() not in _INSECURE_ENVS


# Resolved once at import. ENVIRONMENT does not change within a running process,
# and caching it prevents a runtime os.environ mutation from silently flipping
# authentication off mid-process.
_TOKEN_REQUIRED: bool = _token_required()

# A real deployment token is a long random secret. Reject obvious placeholders
# and anything too short: a guessable/default token (e.g. left at "changeme")
# is no better than no token at all.
_MIN_TOKEN_LEN = 16
_PLACEHOLDER_TOKENS = {
    "test", "tests", "testing", "changeme", "change-me", "change_me",
    "secret", "token", "password", "placeholder", "example", "dummy",
    "calc", "calc-token", "calc-service-token", "your-token-here", "xxx", "todo",
}


def _token_weakness_reason(token: str) -> str | None:
    """Why *token* is unacceptable for a non-dev deployment, or None if it is fine.

    Pure helper so the policy is unit-testable without process-env gymnastics.
    """
    if not token:
        return "unset"
    if token.strip().lower() in _PLACEHOLDER_TOKENS:
        return "a known placeholder value"
    if len(token) < _MIN_TOKEN_LEN:
        return f"too short (<{_MIN_TOKEN_LEN} chars)"
    return None


# Fail fast at import (app startup) rather than on the first request: a non-dev
# deployment with a missing OR weak token is a misconfiguration, not a runtime
# condition. A guessable default would expose every calc endpoint.
if _TOKEN_REQUIRED:
    _weakness = _token_weakness_reason(os.environ.get("CALC_SERVICE_TOKEN", ""))
    if _weakness:
        raise RuntimeError(
            f"CALC_SERVICE_TOKEN is {_weakness} in ENVIRONMENT={_environment()!r}. "
            "The calc-service refuses to start with a missing or weak token outside "
            f"{sorted(_INSECURE_ENVS)}. Set CALC_SERVICE_TOKEN to a long random secret."
        )
else:
    # Auth is intentionally disabled (ENVIRONMENT is in _INSECURE_ENVS).
    # Emit a startup CRITICAL so this insecure mode is impossible to miss in logs.
    logger.critical(
        "Authentication is DISABLED: ENVIRONMENT=%r is in the insecure-env list %r. "
        "ALL calc endpoints are unprotected. "
        "Set ENVIRONMENT to a non-insecure value and supply CALC_SERVICE_TOKEN "
        "before exposing this service.",
        _environment(),
        sorted(_INSECURE_ENVS),
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
        logger.critical(
            "CALC_SERVICE_TOKEN is not set — ALL ENDPOINTS ARE UNPROTECTED. "
            "ENVIRONMENT=%r is in the insecure-env list. "
            "Set CALC_SERVICE_TOKEN to enable authentication.",
            _environment(),
        )
        return

    if not hmac.compare_digest(x_calc_service_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing service token",
        )
