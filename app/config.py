
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from fastapi.templating import Jinja2Templates

# Location of the app folder
BASE_DIR = Path(__file__).resolve().parent

# Load the project's .env by explicit path (existing environment
# values are not overridden). A bare load_dotenv() searches from the
# current directory inside `uvicorn --reload` workers, so starting
# the server outside the project silently skipped .env and the
# admin routes were never registered (404).
ENV_FILE = BASE_DIR.parent / ".env"
ENV_FILE_LOADED = load_dotenv(ENV_FILE)

# Connect the HTML templates folder
templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)


# ==========================================
# SECURITY SETTINGS (Phase P0A.2)
# ==========================================

MIN_ADMIN_KEY_LENGTH = 32

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off", ""}


class SecurityConfigError(RuntimeError):
    """Raised when security settings are missing or unsafe."""


def _read_flag(
    environ: Mapping[str, str],
    name: str,
    default: bool
) -> bool:

    raw = environ.get(name)

    if raw is None:
        return default

    value = raw.strip().lower()

    if value in _TRUE_VALUES:
        return True

    if value in _FALSE_VALUES:
        return False

    # Unknown values fail closed instead of guessing
    raise SecurityConfigError(f"{name} must be 1 or 0.")


def _read_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int
) -> int:

    raw = environ.get(name)

    if raw is None or not raw.strip():
        return default

    try:
        value = int(raw.strip())
    except ValueError:
        raise SecurityConfigError(
            f"{name} must be a whole number."
        ) from None

    if not minimum <= value <= maximum:
        raise SecurityConfigError(
            f"{name} must be between {minimum} and {maximum}."
        )

    return value


@dataclass(frozen=True)
class SecuritySettings:

    # Legal Sources admin page and API (off by default)
    admin_enabled: bool = False

    # repr=False keeps the key out of logs and tracebacks
    admin_key: str | None = field(default=None, repr=False)

    # Browser admin session lifetime (absolute, not sliding)
    admin_session_minutes: int = 30

    # Secure cookie flag; only set 0 for plain-http local testing
    admin_cookie_secure: bool = True

    # Enables /docs, /redoc and /openapi.json
    debug: bool = False

    # AI resource protection (P0B.1). Conservative defaults for a
    # CPU-only Ollama server: one inference at a time, and each
    # client may start 3 analyses per 10 minutes.
    ai_max_concurrent: int = 1
    ai_rate_limit: int = 3
    ai_rate_window_seconds: int = 600

    # TEMPORARY team testing (P0B.1a): allow AI analysis for
    # requests arriving through the local Cloudflare Tunnel.
    # Off by default. Not production authorisation.
    allow_tunnel_ai: bool = False

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None
    ) -> "SecuritySettings":

        env = os.environ if environ is None else environ

        return cls(
            admin_enabled=_read_flag(
                env, "TOR_LAW_ADMIN_ENABLED", default=False
            ),
            admin_key=env.get("TOR_LAW_ADMIN_KEY") or None,
            admin_session_minutes=_read_int(
                env, "TOR_LAW_ADMIN_SESSION_MINUTES",
                default=30, minimum=5, maximum=240
            ),
            admin_cookie_secure=_read_flag(
                env, "TOR_LAW_ADMIN_COOKIE_SECURE", default=True
            ),
            debug=_read_flag(
                env, "TOR_LAW_DEBUG", default=False
            ),
            ai_max_concurrent=_read_int(
                env, "TOR_LAW_AI_MAX_CONCURRENT",
                default=1, minimum=1, maximum=4
            ),
            ai_rate_limit=_read_int(
                env, "TOR_LAW_AI_RATE_LIMIT",
                default=3, minimum=1, maximum=100
            ),
            ai_rate_window_seconds=_read_int(
                env, "TOR_LAW_AI_RATE_WINDOW_SECONDS",
                default=600, minimum=10, maximum=86400
            ),
            allow_tunnel_ai=_read_flag(
                env, "TOR_LAW_ALLOW_TUNNEL_AI", default=False
            ),
        )

    def validate(self) -> None:

        if not self.admin_enabled:
            return

        if not self.admin_key:
            raise SecurityConfigError(
                "TOR_LAW_ADMIN_ENABLED=1 but TOR_LAW_ADMIN_KEY "
                "is not set. Refusing to start."
            )

        if len(self.admin_key) < MIN_ADMIN_KEY_LENGTH:
            raise SecurityConfigError(
                "TOR_LAW_ADMIN_KEY must be at least "
                f"{MIN_ADMIN_KEY_LENGTH} characters."
            )
