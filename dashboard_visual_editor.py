"""Admin-only visual editor for the IronWolf web dashboard.

Visual changes are stored separately from Discord bot configuration and never
execute Python or alter bot commands.
"""
import json
import re
import secrets
import threading
from pathlib import Path

from flask import Blueprint, abort, jsonify, request, send_file, session

ADMIN_DISCORD_ID = "1493110334280564807"
_LOCK = threading.RLock()


def register_visual_editor(app, data_path):
    bp = Blueprint("dashboard_visual_editor", __name__)
    state_file = Path(data_path("dashboard_visual_edits.json"))
    upload_dir = Path(data_path("dashboard_visual_uploads"))
    upload_dir.mkdir(parents=True, exist_ok=True)

    def is_admin():
        return str(session.get("user_id", "")) == ADMIN_DISCORD_ID

    def read_state():
        try:
            value = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, ValueError, TypeError):
            pass
        return {"content": {}, "tierPrices": {}, "layout": {}, "images": {}, "styles": {}}

    def write_state(value):
        state_file.parent.mkdir(parents=True, exist_ok=True)
        temp = state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temp.replace(state_file)

    @bp.get("/api/visual-editor")
    def visual_editor_admin_api():
        if not is_admin():
            return jsonify({"error": "forbidden"}), 403
        with _LOCK:
            data = read_state()
        token = session.get("visual_editor_csrf") or secrets.token_urlsafe(32)
        session["visual_editor_csrf"] = token
        return jsonify({"changes": data.get("content", {}), "tierPrices": data.get("tierPrices", {}),
                        "layout": data.get("layout", {}), "images": data.get("images", {}),
                        "styles": data.get("styles", {}), "csrf": token})

    @bp.post("/api/visual-editor")
    def visual_editor_save_api():
        if not is_admin():
            return jsonify({"error": "forbidden"}), 403
        payload = request.get_json(silent=True) or {}
        token = str(payload.get("csrf", ""))
        expected = str(session.get("visual_editor_csrf", ""))
        if not token or not expected or not secrets.compare_digest(token, expected):
            return jsonify({"error": "invalid csrf token"}), 400
        limits = {"changes": 500, "tierPrices": 100, "layout": 500, "images": 500, "styles": 500}
        cleaned = {}
        for name, limit in limits.items():
            value = payload.get(name, {})
            if not isinstance(value, dict) or len(value) > limit:
                return jsonify({"error": "invalid " + name}), 400
            cleaned[name] = value
        for key, value in cleaned["content"].items():
            if not isinstance(key, str) or len(key) > 600 or not isinstance(value, str) or len(value) > 50000:
                return jsonify({"error": "invalid content"}), 400
        for key, value in cleaned["images"].items():
            if not isinstance(key, str) or len(key) > 600 or not isinstance(value, str) or not value.startswith("/uploads/"):
                return jsonify({"error": "invalid image mapping"}), 400
        allowed = {"color", "background", "borderColor", "borderRadius", "fontSize", "fontFamily",
                   "fontWeight", "fontStyle", "textDecoration", "textAlign", "width", "height",
                   "position", "left", "top", "margin", "zIndex", "display", "objectFit"}
        for key, style in cleaned["styles"].items():
            if not isinstance(key, str) or len(key) > 600 or not isinstance(style, dict):
                return jsonify({"error": "invalid style"}), 400
            for prop, value in style.items():
                if prop not in allowed or not isinstance(value, str) or len(value) > 180:
                    return jsonify({"error": "invalid style property"}), 400
                if re.search(r"url\\s*\\(|expression|javascript", value, re.I):
                    return jsonify({"error": "invalid style value"}), 400
        for key, pos in cleaned["layout"].items():
            if not isinstance(key, str) or not isinstance(pos, dict):
                return jsonify({"error": "invalid layout"}), 400
            try:
                if abs(float(pos.get("x", 0))) > 3000 or abs(float(pos.get("y", 0))) > 3000:
                    raise ValueError()
            except (TypeError, ValueError):
                return jsonify({"error": "invalid position"}), 400
        with _LOCK:
            write_state(cleaned)
        return jsonify({"ok": True, "saved": True})

    @bp.get("/api/visual-edits-public")
    def visual_editor_public_api():
        with _LOCK:
            data = read_state()
        response = jsonify(data)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @bp.post("/api/visual-editor/upload")
    def visual_editor_upload_api():
        if not is_admin():
            return jsonify({"error": "forbidden"}), 403
        token = str(request.form.get("csrf") or request.headers.get("X-Editor-CSRF") or "")
        expected = str(session.get("visual_editor_csrf", ""))
        if not token or not expected or not secrets.compare_digest(token, expected):
            return jsonify({"error": "invalid csrf token"}), 400
        uploaded = request.files.get("image")
        if not uploaded:
            return jsonify({"error": "Please choose an image."}), 400
        data = uploaded.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            return jsonify({"error": "Image is too large."}), 413
        ext = str(uploaded.filename or "").rsplit(".", 1)[-1].lower()
        signatures = {"png": data.startswith(bytes([137,80,78,71,13,10,26,10])),
                      "jpg": data.startswith(bytes([255,216,255])),
                      "jpeg": data.startswith(bytes([255,216,255])),
                      "gif": data.startswith((b"GIF87a", b"GIF89a")),
                      "webp": len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"}
        if ext not in signatures or not signatures[ext]:
            return jsonify({"error": "Unsupported or invalid image."}), 400
        name = secrets.token_hex(16) + "." + ext
        (upload_dir / name).write_bytes(data)
        return jsonify({"ok": True, "url": "/uploads/" + name})

    @bp.get("/uploads/<path:filename>")
    def visual_editor_upload_file(filename):
        if not re.fullmatch(r"[a-f0-9]{32}\\.(png|jpg|jpeg|gif|webp)", filename):
            abort(404)
        path = upload_dir / filename
        if not path.is_file():
            abort(404)
        return send_file(path)

    app.register_blueprint(bp)
