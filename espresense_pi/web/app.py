"""Flask web application providing an ESPresense-style configuration UI
and a compatible REST API (see
https://espresense.com/configuration/rest-api/ for the reference this
mirrors).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

from espresense_pi.pairing import enter_pairing_mode_sync

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

APP_VERSION = "0.1.0"

# Mirrors the device type list from upstream ESPresense's own enrollment UI
# (ui/src/routes/devices/+page.svelte).
DEVICE_TYPES = [
    "watch", "wallet", "ipad", "phone", "airpods", "laptop",
    "node", "keys", "therm", "flora", "tile",
]

logger = logging.getLogger(__name__)


def _kebab_case_id(name: str, device_type: str = "") -> str:
    words = [w for w in name.lower().split() if w != device_type.lower()]
    slug = "-".join(words).replace("'", "")
    slug = re.sub(r"[^a-z0-9-]+", "-", slug)
    return re.sub(r"^-+|-+$", "", slug)


def create_app(config, store, live_state, restart_fn, mqtt_client):
    app = Flask(
        __name__,
        template_folder=str(TEMPLATE_DIR),
        static_folder=str(STATIC_DIR),
    )

    @app.route("/")
    def index():
        return redirect(url_for("network_page"))

    @app.route("/network", methods=["GET", "POST"])
    def network_page():
        if request.method == "POST":
            new_room = request.form.get("room_name", "").strip() or config.room_name
            config.update_section("room", {"name": new_room})
            config.update_section("mqtt", {
                "host": request.form.get("mqtt_host", "").strip(),
                "port": int(request.form.get("mqtt_port", 1883) or 1883),
                "username": request.form.get("mqtt_username", "").strip(),
                "password": request.form.get("mqtt_password", ""),
                "discovery": request.form.get("discovery") == "on",
                "discovery_prefix": request.form.get("discovery_prefix", "homeassistant").strip(),
                "pub_tele": request.form.get("pub_tele") == "on",
                "pub_devices": request.form.get("pub_devices") == "on",
            })
            mqtt_client.reconnect()
            return redirect(url_for("network_page", saved=1))
        return render_template(
            "network.html",
            active="network",
            room=config.get_section("room"),
            mqtt=config.get_section("mqtt"),
            saved=request.args.get("saved"),
        )

    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        if request.method == "POST":
            form = request.form
            config.update_section("ble", {
                "ref_rssi": int(form.get("ref_rssi", -65)),
                "tx_ref_rssi": int(form.get("tx_ref_rssi", -59)),
                "rx_adj_rssi": int(form.get("rx_adj_rssi", 0)),
                "absorption": float(form.get("absorption", 2.7)),
                "max_distance": float(form.get("max_distance", 16.0)),
                "skip_distance": float(form.get("skip_distance", 0.5)),
                "skip_ms": int(form.get("skip_ms", 5000)),
                "forget_ms": int(form.get("forget_ms", 150000)),
                "include": form.get("include", "").strip(),
                "exclude": form.get("exclude", "").strip(),
                "known_macs": form.get("known_macs", "").strip(),
                "count_ids": form.get("count_ids", "").strip(),
                "count_enter": float(form.get("count_enter", 2.0)),
                "count_exit": float(form.get("count_exit", 4.0)),
                "count_ms": int(form.get("count_ms", 10000)),
            })
            mqtt_client.publish_snapshot()
            return redirect(url_for("settings_page", saved=1))
        return render_template(
            "settings.html",
            active="settings",
            ble=config.get_section("ble"),
            saved=request.args.get("saved"),
        )

    @app.route("/devices", methods=["GET"])
    def devices_page():
        return render_template(
            "devices.html",
            active="devices",
            live_devices=sorted(live_state.snapshot(), key=lambda d: d.get("distance", 999)),
            configs=store.list(),
            device_types=DEVICE_TYPES,
            pair_ok=request.args.get("pair_ok"),
            pair_error=request.args.get("pair_error"),
            pair_error_msg=request.args.get("pair_error_msg"),
        )

    @app.route("/devices/enroll", methods=["POST"])
    def enroll_device():
        device_id = request.form.get("id", "").strip()
        original_id = request.form.get("original_id", "").strip()
        alias = request.form.get("alias", "").strip()
        name = request.form.get("name", "").strip()
        rssi_at_1m = request.form.get("rssi_at_1m", "").strip()

        if not device_id:
            return redirect(url_for("devices_page"))

        entry = {"id": device_id, "alias": alias or device_id, "name": name}
        if rssi_at_1m:
            try:
                entry["rssi@1m"] = int(rssi_at_1m)
            except ValueError:
                pass
        store.upsert(entry)

        if original_id and original_id != device_id:
            store.delete(original_id)

        return redirect(url_for("devices_page"))

    @app.route("/devices/pair", methods=["POST"])
    def pair_device():
        existing_id = request.form.get("existing_id", "").strip()
        device_type = request.form.get("device_type", "").strip()
        name = request.form.get("name", "").strip()

        if not name:
            return redirect(url_for("devices_page", pair_error="(missing name)"))

        if existing_id:
            existing = store.get(existing_id) or {}
            device_id = existing_id
            alias = existing.get("alias", existing_id)
        else:
            device_id = f"{device_type}:{_kebab_case_id(name, device_type)}" if device_type else _kebab_case_id(name)
            alias = device_id

        if not device_id or device_id.endswith(":"):
            return redirect(url_for("devices_page", pair_error="(could not generate an id from that name)"))

        try:
            mac = enter_pairing_mode_sync(timeout=60.0)
        except Exception as exc:
            logger.exception("Bluetooth pairing failed")
            msg = str(exc) or type(exc).__name__
            return redirect(url_for("devices_page", pair_error=device_id, pair_error_msg=msg[:200]))

        store.upsert({"id": device_id, "alias": alias, "name": name, "mac": mac})
        return redirect(url_for("devices_page", pair_ok=device_id))

    @app.route("/devices/delete/<path:device_id>", methods=["POST"])
    def delete_device(device_id):
        store.delete(device_id)
        return redirect(url_for("devices_page"))

    @app.route("/restart", methods=["POST"])
    @app.route("/reboot", methods=["POST"])
    def restart():
        restart_fn()
        return "Restarting...", 200

    # ---- REST API (mirrors espresense.com/configuration/rest-api) -----
    @app.route("/json")
    def json_root():
        return jsonify({"room": config.room_name, "ver": APP_VERSION, "firm": "rpi"})

    @app.route("/json/devices")
    def json_devices():
        show_all = request.args.get("showAll") == "1"
        devices = live_state.snapshot()
        if not show_all:
            devices = [d for d in devices if d.get("visible")]
        return jsonify({
            "room": config.room_name,
            "ver": APP_VERSION,
            "firm": "rpi",
            "devices": devices,
        })

    @app.route("/json/configs", methods=["GET", "POST", "DELETE"])
    def json_configs():
        if request.method == "GET":
            return jsonify({
                "room": config.room_name,
                "ver": APP_VERSION,
                "firm": "rpi",
                "configs": store.list(),
            })
        if request.method == "POST":
            try:
                entry = store.upsert(request.get_json(force=True))
                return jsonify({"success": True, "config": entry})
            except Exception as exc:
                return jsonify({"error": str(exc)}), 400
        device_id = request.args.get("id", "")
        if not device_id:
            return jsonify({"error": "id is required"}), 400
        store.delete(device_id)
        return jsonify({"success": True})

    return app
