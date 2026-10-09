#!/usr/bin/env python3
"""Flask service for provenance artifact lookup."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote

from flask import Flask, jsonify, redirect, render_template, request

from db_utils import (
    DatabaseError,
    get_gene_set_details_by_id,
    get_gene_set_document_by_id,
    get_provenance_by_id,
    get_trait_by_legacy_id,
    list_artifacts,
    list_bottom_line_by_trait,
    list_gene_set_documents,
    list_gene_sets_by_document_id,
    list_traits,
    list_traits_full,
)


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
    database_file = DEFAULT_DATABASE.resolve()
    log_file = Path(os.environ.get("WS_PROVENANCE_LOG", str(DEFAULT_LOG_FILE))).expanduser().resolve()

    configure_logging(log_file)
    app.config["DATABASE_FILE"] = database_file
    app.config["LOG_FILE"] = log_file

    @app.before_request
    def log_request() -> None:
        logging.info("REST %s %s from %s", request.method, request.full_path, request.remote_addr)

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/home/botton_line")
    def bottom_line_home():
        try:
            trait_rows = list_traits(app.config["DATABASE_FILE"])
        except DatabaseError as exc:
            logging.error("Database error in bottom-line home trait list: %s", exc)
            return render_template("bottom_line_home.html", traits=[], error=str(exc)), 500

        return render_template("bottom_line_home.html", traits=trait_rows, error=None)

    @app.get("/home/gene_set")
    def gene_set_home():
        try:
            documents = list_gene_set_documents(app.config["DATABASE_FILE"])
        except DatabaseError as exc:
            logging.error("Database error in gene-set home document list: %s", exc)
            return render_template("gene_set_home.html", documents=[], error=str(exc)), 500

        return render_template("gene_set_home.html", documents=documents, error=None)

    @app.get("/artifact/<path:artifact_id>")
    def artifact_detail(artifact_id: str):
        return render_template("artifact_detail.html", artifact_id=artifact_id)

    @app.get("/gene_set/details/id=<path:input_geneset_id>")
    def gene_set_detail(input_geneset_id: str):
        gene_set_id = input_geneset_id.strip()
        if not gene_set_id:
            logging.error("Missing or empty gene set id path parameter in /gene_set/details")
            return render_template(
                "gene_set_detail.html",
                gene_set=None,
                error="Gene set id is required.",
            ), 400

        try:
            gene_set = get_gene_set_details_by_id(app.config["DATABASE_FILE"], gene_set_id)
        except DatabaseError as exc:
            logging.error("Database error in /gene_set/details for id %s: %s", gene_set_id, exc)
            return render_template(
                "gene_set_detail.html",
                gene_set=None,
                error=str(exc),
            ), 500

        if gene_set is None:
            logging.error("Gene set id not found in /gene_set/details: %s", gene_set_id)
            return render_template(
                "gene_set_detail.html",
                gene_set=None,
                error=f"No gene set record found for id '{gene_set_id}'.",
            ), 404

        return render_template("gene_set_detail.html", gene_set=gene_set, error=None)

    @app.get("/gene_set/list/id=<path:input_gene_collection_id>")
    def gene_set_collection_list(input_gene_collection_id: str):
        collection_id = input_gene_collection_id.strip()
        if not collection_id:
            logging.error("Missing or empty collection id path parameter in /gene_set/list")
            return render_template(
                "gene_set_list.html",
                document=None,
                gene_sets=[],
                error="Gene set collection id is required.",
            ), 400

        try:
            document = get_gene_set_document_by_id(app.config["DATABASE_FILE"], collection_id)
            gene_sets = list_gene_sets_by_document_id(app.config["DATABASE_FILE"], collection_id)
        except DatabaseError as exc:
            logging.error("Database error in /gene_set/list for id %s: %s", collection_id, exc)
            return render_template(
                "gene_set_list.html",
                document=None,
                gene_sets=[],
                error=str(exc),
            ), 500

        if document is None:
            logging.error("Gene set collection id not found in /gene_set/list: %s", collection_id)
            return render_template(
                "gene_set_list.html",
                document=None,
                gene_sets=[],
                error=f"No gene set collection found for id '{collection_id}'.",
            ), 404

        return render_template("gene_set_list.html", document=document, gene_sets=gene_sets, error=None)

    @app.get("/bottom_line/<input_trait>/<input_ancestry>")
    def bottom_line_trait_ancestry(input_trait: str, input_ancestry: str):
        trait = input_trait.strip()
        ancestry = input_ancestry.strip()
        if not trait or not ancestry:
            logging.error("Missing or empty trait/ancestry path parameter in /bottom_line")
            return render_template(
                "bottom_line.html",
                trait=trait or input_trait,
                trait_name=trait or input_trait,
                ancestry=ancestry or input_ancestry,
                artifacts=[],
                error="Trait and ancestry are required.",
            ), 400

        try:
            trait_row = get_trait_by_legacy_id(app.config["DATABASE_FILE"], trait)
            artifacts = list_bottom_line_by_trait(app.config["DATABASE_FILE"], trait, ancestry)
        except DatabaseError as exc:
            logging.error("Database error in /bottom_line/%s/%s: %s", trait, ancestry, exc)
            return render_template(
                "bottom_line.html",
                trait=trait,
                trait_name=trait,
                ancestry=ancestry,
                artifacts=[],
                error=str(exc),
            ), 500

        if len(artifacts) == 1:
            return redirect(f"../../artifact/{quote(artifacts[0]['id'], safe='')}")

        return render_template(
            "bottom_line.html",
            trait=trait,
            trait_name=(trait_row or {}).get("name") or trait,
            ancestry=ancestry,
            artifacts=artifacts,
            error=None,
        )

    @app.get("/bottom_line/<path:input_trait>")
    def bottom_line_trait(input_trait: str):
        trait = input_trait.strip()
        if not trait:
            logging.error("Missing or empty trait path parameter in /bottom_line")
            return render_template(
                "bottom_line.html",
                trait=input_trait,
                trait_name=input_trait,
                ancestry=None,
                artifacts=[],
                error="Trait is required.",
            ), 400

        try:
            trait_row = get_trait_by_legacy_id(app.config["DATABASE_FILE"], trait)
            artifacts = list_bottom_line_by_trait(app.config["DATABASE_FILE"], trait)
        except DatabaseError as exc:
            logging.error("Database error in /bottom_line/%s: %s", trait, exc)
            return render_template(
                "bottom_line.html",
                trait=trait,
                trait_name=trait,
                ancestry=None,
                artifacts=[],
                error=str(exc),
            ), 500

        return render_template(
            "bottom_line.html",
            trait=trait,
            trait_name=(trait_row or {}).get("name") or trait,
            ancestry=None,
            artifacts=artifacts,
            error=None,
        )

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
            artifacts = list_artifacts(app.config["DATABASE_FILE"], limit)
        except DatabaseError as exc:
            logging.error("Database error in /list: %s", exc)
            return jsonify({"error": "database_error", "message": str(exc)}), 500

        return jsonify(artifacts)

    @app.get("/traits")
    def traits():
        try:
            trait_rows = list_traits(app.config["DATABASE_FILE"])
        except DatabaseError as exc:
            logging.error("Database error in /traits: %s", exc)
            return jsonify({"error": "database_error", "message": str(exc)}), 500

        return jsonify(trait_rows)

    @app.get("/ws/bottom_line/trait_list")
    def bottom_line_trait_list():
        try:
            trait_rows = list_traits(app.config["DATABASE_FILE"])
        except DatabaseError as exc:
            logging.error("Database error in /ws/bottom_line/trait_list: %s", exc)
            return jsonify({"error": "database_error", "message": str(exc)}), 500

        return jsonify(trait_rows)

    @app.get("/ws/bottom_line/trait_list_full")
    def bottom_line_trait_list_full():
        try:
            trait_rows = list_traits_full(app.config["DATABASE_FILE"])
        except DatabaseError as exc:
            logging.error("Database error in /ws/bottom_line/trait_list_full: %s", exc)
            return jsonify({"error": "database_error", "message": str(exc)}), 500

        return jsonify(trait_rows)

    @app.get("/ws/bottom_line")
    def bottom_line_trait_data():
        trait = request.args.get("trait", "").strip()
        ancestry = request.args.get("ancestry", "").strip() or None
        if not trait:
            logging.error("Missing or empty trait parameter in /ws/bottom_line")
            return jsonify({"error": "missing_trait", "message": "Query parameter 'trait' is required."}), 400

        try:
            artifacts = list_bottom_line_by_trait(app.config["DATABASE_FILE"], trait, ancestry)
        except DatabaseError as exc:
            logging.error("Database error in /ws/bottom_line for trait %s ancestry %s: %s", trait, ancestry, exc)
            return jsonify({"error": "database_error", "message": str(exc)}), 500

        return jsonify(artifacts)

    @app.get("/ws/gene_set/details/id=<path:input_geneset_id>")
    def gene_set_detail_data(input_geneset_id: str):
        gene_set_id = input_geneset_id.strip()
        if not gene_set_id:
            logging.error("Missing or empty gene set id path parameter in /ws/gene_set/details")
            return jsonify({"error": "missing_id", "message": "Gene set id is required."}), 400

        try:
            gene_set = get_gene_set_details_by_id(app.config["DATABASE_FILE"], gene_set_id)
        except DatabaseError as exc:
            logging.error("Database error in /ws/gene_set/details for id %s: %s", gene_set_id, exc)
            return jsonify({"error": "database_error", "message": str(exc)}), 500

        if gene_set is None:
            logging.error("Gene set id not found in /ws/gene_set/details: %s", gene_set_id)
            return jsonify({"error": "not_found", "message": f"No gene set record found for id '{gene_set_id}'."}), 404

        return jsonify(gene_set)

    @app.get("/get_provenance")
    def get_provenance():
        artifact_id = request.args.get("id", "").strip()
        if not artifact_id:
            logging.error("Missing or empty id parameter in /get_provenance")
            return jsonify({"error": "missing_id", "message": "Query parameter 'id' is required."}), 400

        try:
            artifact = get_provenance_by_id(app.config["DATABASE_FILE"], artifact_id)
        except DatabaseError as exc:
            logging.error("Database error in /get_provenance for id %s: %s", artifact_id, exc)
            return jsonify({"error": "database_error", "message": str(exc)}), 500

        if artifact is None:
            logging.error("Provenance id not found in /get_provenance: %s", artifact_id)
            return jsonify({"error": "not_found", "message": f"No provenance record found for id '{artifact_id}'."}), 404

        return jsonify(artifact)

    @app.get("/ws/get_provenance")
    def get_ws_provenance():
        artifact_id = request.args.get("id", "").strip()
        if not artifact_id:
            logging.error("Missing or empty id parameter in /get_provenance")
            return jsonify({"error": "missing_id", "message": "Query parameter 'id' is required."}), 400

        try:
            artifact = get_provenance_by_id(app.config["DATABASE_FILE"], artifact_id)
        except DatabaseError as exc:
            logging.error("Database error in /get_provenance for id %s: %s", artifact_id, exc)
            return jsonify({"error": "database_error", "message": str(exc)}), 500

        if artifact is None:
            logging.error("Provenance id not found in /get_provenance: %s", artifact_id)
            return jsonify({"error": "not_found", "message": f"No provenance record found for id '{artifact_id}'."}), 404

        return jsonify(artifact)

    @app.get("/drs/v1/objects/")
    def drs_objects_root():
        return jsonify({"current_time": datetime.now(timezone.utc).isoformat()})

    return app


app = create_app()


if __name__ == "__main__":
    port = parse_port(os.environ.get("WS_PROVENANCE_PORT"))
    app.run(host="0.0.0.0", port=port)
