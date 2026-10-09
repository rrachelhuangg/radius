"""Map pins with a sticky note each, saved to the signed-in user's account."""

from flask import Blueprint, g, jsonify, request

from auth import current_user, db

MAX_PINS = 500
MAX_NOTE = 5000
MAX_LABEL = 200
FIELDS = "id, lat, lon, label, note, updated_at"

bp = Blueprint("pins", __name__)


def pin_json(pin):
    return {**pin, "updated_at": pin["updated_at"].isoformat()}


def json_body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


@bp.before_request
def require_user():
    g.user = current_user()
    if g.user is None:
        return jsonify(error="Sign in to save pins."), 401
    # Browsers can't send JSON to another site without its permission, so this also blocks forged requests
    if request.method in ("POST", "PATCH") and not request.is_json:
        return jsonify(error="Expected JSON."), 415


@bp.get("/api/pins")
def list_pins():
    with db() as conn:
        pins = conn.execute(
            f"SELECT {FIELDS} FROM pins WHERE user_id = %s ORDER BY created_at", (g.user["id"],)
        ).fetchall()
    return jsonify(pins=[pin_json(p) for p in pins])


@bp.post("/api/pins")
def add_pin():
    body = json_body()
    try:
        lat, lon = float(body["lat"]), float(body["lon"])
    except (KeyError, TypeError, ValueError):
        lat = lon = None
    if lat is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return jsonify(error="Invalid position."), 400
    label = str(body.get("label") or f"{lat:.5f}, {lon:.5f}")[:MAX_LABEL]

    with db() as conn:
        count = conn.execute("SELECT count(*) AS n FROM pins WHERE user_id = %s", (g.user["id"],)).fetchone()["n"]
        if count >= MAX_PINS:
            return jsonify(error=f"You can have up to {MAX_PINS} pins."), 400
        pin = conn.execute(
            f"INSERT INTO pins (user_id, lat, lon, label) VALUES (%s, %s, %s, %s) RETURNING {FIELDS}",
            (g.user["id"], lat, lon, label),
        ).fetchone()
    return jsonify(pin=pin_json(pin)), 201


@bp.patch("/api/pins/<int:pin_id>")
def update_pin(pin_id):
    """Change a pin's note and/or its name (label)."""
    body = json_body()
    changes = {field: body[field] for field in ("note", "label") if field in body}
    if not changes or not all(isinstance(v, str) for v in changes.values()):
        return jsonify(error="Nothing to update."), 400
    if len(changes.get("note", "")) > MAX_NOTE:
        return jsonify(error=f"Notes can be up to {MAX_NOTE} characters."), 400
    if "label" in changes:
        changes["label"] = changes["label"].strip()[:MAX_LABEL]
        if not changes["label"]:
            return jsonify(error="Pin names can't be blank."), 400

    columns = ", ".join(f"{field} = %s" for field in changes)  # field names come from the fixed list above
    with db() as conn:
        pin = conn.execute(
            f"UPDATE pins SET {columns}, updated_at = now() WHERE id = %s AND user_id = %s RETURNING {FIELDS}",
            (*changes.values(), pin_id, g.user["id"]),
        ).fetchone()
    if pin is None:
        return jsonify(error="Pin not found."), 404
    return jsonify(pin=pin_json(pin))


@bp.delete("/api/pins/<int:pin_id>")
def delete_pin(pin_id):
    with db() as conn:
        deleted = conn.execute("DELETE FROM pins WHERE id = %s AND user_id = %s", (pin_id, g.user["id"])).rowcount
    if not deleted:
        return jsonify(error="Pin not found."), 404
    return "", 204
