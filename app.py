"""Home Assistant Flask application entry point."""

from __future__ import annotations

import logging
import os

from flask import Flask, render_template
from flask_cors import CORS

from home_assistant.api.routes import api, device_scanner, plugin_manager, wlan_manager

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
    app.config.update(config or {})
    CORS(app)

    # Register blueprints
    app.register_blueprint(api)

    # Serve the single-page dashboard
    @app.get("/")
    def index():
        return render_template("index.html")

    logger.info("Home Assistant application created")
    return app


if __name__ == "__main__":
    host = os.environ.get("HA_HOST", "0.0.0.0")
    port = int(os.environ.get("HA_PORT", "5000"))
    debug = os.environ.get("HA_DEBUG", "0") == "1"
    flask_app = create_app()
    logger.info("Starting server on http://%s:%d", host, port)
    flask_app.run(host=host, port=port, debug=debug)
