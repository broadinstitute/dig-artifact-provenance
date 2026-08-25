#!/usr/bin/env python3
"""Flask service for provenance artifact lookup."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask, jsonify, request

from db_utils import DatabaseError, list_artifact_ids


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = APP_ROOT / "data" / "provenance_db.sqlite"
DEFAULT_LOG_FILE = APP_ROOT / "logs" / "ws_provenance.log"
DEFAULT_PORT = 8080


def parse_port(value: str | None) -> int:
    if not value:
        return DEFAULT_PORT

    try:
        return int(value)
    except ValueError:
        return DEFAULT_PORT


def configure_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )


def create_app() -> Flask:
    app = Flask(__name__)
    database_file = Path(os.environ.get("WS_PROVENANCE_DB", str(DEFAULT_DATABASE))).expanduser().resolve()
    log_file = Path(os.environ.get("WS_PROVENANCE_LOG", str(DEFAULT_LOG_FILE))).expanduser().resolve()

    configure_logging(log_file)
    app.config["DATABASE_FILE"] = database_file
    app.config["LOG_FILE"] = log_file

    @app.before_request
    def log_request() -> None:
        logging.info("REST %s %s from %s", request.method, request.full_path, request.remote_addr)

    @app.errorhandler(Exception)
    def handle_exception(exc: Exception):
        logging.exception("Unhandled REST error: %s", exc)
        return jsonify({"error": "internal_server_error", "message": "An internal server error occurred."}), 500

    @app.get("/list")
    def list_ids():
        limit_value = request.args.get("limit", "5000")
        try:
            limit = int(limit_value)
            if limit <= 0:
                raise ValueError("limit must be positive")
        except ValueError:
            logging.error("Invalid limit parameter: %s", limit_value)
            return jsonify({"error": "invalid_limit", "message": "Query parameter 'limit' must be a positive integer."}), 400

        try:
            artifact_ids = list_artifact_ids(app.config["DATABASE_FILE"], limit)
        except DatabaseError as exc:
            logging.error("Database error in /list: %s", exc)
            return jsonify({"error": "database_error", "message": str(exc)}), 500

        return jsonify(artifact_ids)

    return app


app = create_app()


if __name__ == "__main__":
    port = parse_port(os.environ.get("WS_PROVENANCE_PORT"))
    app.run(host="0.0.0.0", port=port)
