"""Home Assistant Flask application entry point."""

from __future__ import annotations

import logging
import os

from flask import Flask, render_template
from flask_cors import CORS

from home_assistant.api.routes import api, device_scanner, plugin_manager, wlan_manager
from home_assistant.api.routes import limiter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app(config: dict | None = None) -> Flask:
    """Application factory."""
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
    )
    app.config.update(
        {
            "HA_REQUIRE_AUTH": os.environ.get("HA_REQUIRE_AUTH", "1") == "1",
            "HA_API_TOKEN": os.environ.get("HA_API_TOKEN", ""),
            "HA_DEMO_MODE": os.environ.get("HA_DEMO_MODE", "0") == "1",
            # Used by Flask-Limiter; default to in-memory storage.
            "RATELIMIT_STORAGE_URI": os.environ.get(
                "RATELIMIT_STORAGE_URI", "memory://"
            ),
        }
    )
    app.config.update(config or {})

    # Keep tests simple unless explicitly testing auth behavior.
    if app.config.get("TESTING") and "HA_REQUIRE_AUTH" not in (config or {}):
        app.config["HA_REQUIRE_AUTH"] = False

    # If auth is required but no token exists, fall back to no-auth mode.
    # This avoids a broken local UX where protected endpoints always return 503.
    # NOTE: for non-local binds this is a hard failure (see __main__ below).
    if app.config.get("HA_REQUIRE_AUTH") and not str(app.config.get("HA_API_TOKEN", "")).strip():
        logger.warning(
            "HA_REQUIRE_AUTH is enabled but HA_API_TOKEN is not configured; "
            "disabling auth for this runtime."
        )
        app.config["HA_REQUIRE_AUTH"] = False

    CORS(app)

    # Initialise rate limiter with the app
    limiter.init_app(app)

    # Register blueprints
    app.register_blueprint(api)

    # Serve the single-page dashboard
    @app.get("/")
    def index():
        return render_template("index.html")

    logger.info("Home Assistant application created")
    return app


if __name__ == "__main__":
    host = os.environ.get("HA_HOST", "127.0.0.1")
    port = int(os.environ.get("HA_PORT", "5000"))
    debug = os.environ.get("HA_DEBUG", "0") == "1"

    _is_local = host in {"127.0.0.1", "localhost"}

    # Non-local binds require explicit opt-in.
    if not _is_local:
        if os.environ.get("HA_ALLOW_NONLOCAL_BIND", "0") != "1":
            raise RuntimeError(
                "Refusing to bind to non-local address without HA_ALLOW_NONLOCAL_BIND=1"
            )
        # Non-local bind: API token MUST be configured for safety.
        if not os.environ.get("HA_API_TOKEN", "").strip():
            raise RuntimeError(
                "Refusing to start on a non-local address without HA_API_TOKEN set. "
                "Set HA_API_TOKEN to a secret value before exposing this service on the LAN."
            )
        logger.info("Non-local bind on %s – API token authentication is active.", host)
    else:
        logger.info(
            "Running in localhost dev mode (http://%s:%d). "
            "Set HA_HOST and HA_API_TOKEN to expose securely on the LAN.",
            host,
            port,
        )

    flask_app = create_app()
    logger.info("Starting server on http://%s:%d", host, port)
    flask_app.run(host=host, port=port, debug=debug)
