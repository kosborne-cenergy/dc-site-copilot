"""Cloud Run entrypoint — serves the built DC Site Copilot app (dist/) + (if present) parcel APIs."""
import os, pathlib
from flask import Flask, send_from_directory

DIST = pathlib.Path(__file__).parent / "dist"
app = Flask(__name__)


@app.route("/")
def index():
    return send_from_directory(DIST, "index.html")


@app.route("/healthz")
def healthz():
    return {"ok": True}


@app.route("/<path:p>")
def static_files(p):
    return send_from_directory(DIST, p)


# Optional: mount the interactive parcel pipeline APIs if the blueprint is deployed.
try:
    from parcel_app import bp as parcel_bp  # noqa
    app.register_blueprint(parcel_bp)
except Exception as e:
    app.logger.info("parcel APIs not mounted: %s", e)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
