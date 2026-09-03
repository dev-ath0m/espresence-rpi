"""Flask web application providing an ESPresense-style configuration UI
and a compatible REST API (see
https://espresense.com/configuration/rest-api/ for the reference this
mirrors).
"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

from espresense_pi.pairing import pair_sync

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

APP_VERSION = "0.1.0"

logger = logging.getLogger(__name__)


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
            pair_ok=request.args.get("pair_ok"),
            pair_error=request.args.get("pair_error"),
            pair_error_msg=request.args.get("pair_error_msg"),
        )

    @app.route("/devices/enroll", methods=["POST"])
    def enroll_device():
        action = request.form.get("action", "enroll")
        device_id = request.form.get("id", "").strip()
        original_id = request.form.get("original_id", "").strip()
        mac = request.form.get("mac", "").strip()
        alias = request.form.get("alias", "").strip()
        name = request.form.get("name", "").strip()
        rssi_at_1m = request.form.get("rssi_at_1m", "").strip()

        if action == "pair":
            if not mac:
                return redirect(url_for("devices_page", pair_error="(missing MAC)"))
            try:
                pair_sync(mac)
            except Exception as exc:
                logger.exception("Bluetooth pairing with %s failed", mac)
                msg = str(exc) or type(exc).__name__
                return redirect(url_for("devices_page", pair_error=mac, pair_error_msg=msg[:200]))
            mac_clean = mac.replace(":", "").lower()
            known_macs = set((config.get("ble", "known_macs", "") or "").split())
            known_macs.add(mac_clean)
            config.set("ble", "known_macs", " ".join(sorted(known_macs)))
            mqtt_client.publish_snapshot()
            device_id = f"known:{mac_clean}"
            if not alias:
                alias = device_id

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

        if action == "pair":
            return redirect(url_for("devices_page", pair_ok=device_id))
        return redirect(url_for("devices_page"))

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
