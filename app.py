import base64
import hashlib
import json
import os
import secrets
import tempfile
import time
from typing import Any, Dict, List, Tuple
from urllib.parse import urlencode

import requests
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    stream_with_context,
    url_for,
)
from werkzeug.utils import secure_filename

def load_local_config() -> Dict[str, Any]:
    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.local.json"
    )
    if not os.path.exists(config_path):
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            parsed = json.load(config_file)
        if isinstance(parsed, dict):
            return parsed
    except (OSError, json.JSONDecodeError):
        pass

    return {}


LOCAL_CONFIG = load_local_config()
DEFAULT_PORT = int(os.getenv("PORT", str(LOCAL_CONFIG.get("port", 8080))))
ETSY_CLIENT_ID = os.getenv(
    "ETSY_CLIENT_ID", str(LOCAL_CONFIG.get("etsy_client_id", ""))
).strip()
ETSY_REDIRECT_URI = os.getenv(
    "ETSY_REDIRECT_URI",
    str(LOCAL_CONFIG.get("etsy_redirect_uri", f"http://localhost:{DEFAULT_PORT}/callback")),
).strip()
ETSY_OAUTH_URL = "https://www.etsy.com/oauth/connect"
ETSY_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
ETSY_API_BASE_URL = "https://api.etsy.com/v3"
ETSY_SCOPES = "listings_r listings_w"
UPLOAD_DELAY_SECONDS = 2

app = Flask(__name__)
app.config["SECRET_KEY"] = (
    os.getenv("FLASK_SECRET_KEY", str(LOCAL_CONFIG.get("flask_secret_key", ""))).strip()
    or "dev-secret-change-me"
)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB


class EtsyAPIError(Exception):
    """Raised when Etsy API calls fail."""


def generate_pkce_pair() -> Tuple[str, str]:
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return code_verifier, code_challenge


def store_token(token_data: Dict[str, Any]) -> None:
    session["access_token"] = token_data.get("access_token")

    # Etsy may or may not rotate refresh tokens on refresh requests.
    refresh_token = token_data.get("refresh_token")
    if refresh_token:
        session["refresh_token"] = refresh_token

    expires_in = int(token_data.get("expires_in", 0) or 0)
    if expires_in > 0:
        session["token_expires_at"] = int(time.time()) + expires_in - 60

    session.modified = True


def refresh_access_token() -> str:
    refresh_token = session.get("refresh_token")
    if not refresh_token:
        raise EtsyAPIError("Session expired and no refresh token is available.")

    payload = {
        "grant_type": "refresh_token",
        "client_id": ETSY_CLIENT_ID,
        "refresh_token": refresh_token,
    }

    response = requests.post(ETSY_TOKEN_URL, data=payload, timeout=30)
    if response.status_code >= 400:
        raise EtsyAPIError(
            f"Failed to refresh Etsy access token ({response.status_code}): {response.text}"
        )

    token_data = response.json()
    store_token(token_data)

    access_token = token_data.get("access_token")
    if not access_token:
        raise EtsyAPIError("Refresh succeeded but access token was missing in response.")
    return access_token


def get_valid_access_token() -> str:
    access_token = session.get("access_token")
    if not access_token:
        raise EtsyAPIError("Not authenticated. Connect to Etsy first.")

    expires_at = session.get("token_expires_at")
    if expires_at and int(time.time()) >= int(expires_at):
        return refresh_access_token()

    return access_token


def etsy_request(method: str, endpoint: str, access_token: str, **kwargs: Any) -> Dict[str, Any]:
    if not ETSY_CLIENT_ID:
        raise EtsyAPIError(
            "Missing Etsy client ID. Set ETSY_CLIENT_ID or add etsy_client_id in config.local.json."
        )

    url = f"{ETSY_API_BASE_URL}{endpoint}"
    extra_headers = kwargs.pop("headers", {})

    headers = {
        "Authorization": f"Bearer {access_token}",
        "x-api-key": ETSY_CLIENT_ID,
        **extra_headers,
    }

    response = requests.request(method, url, headers=headers, timeout=60, **kwargs)

    if response.status_code >= 400:
        try:
            err_payload = response.json()
            err_message = json.dumps(err_payload)
        except ValueError:
            err_message = response.text
        raise EtsyAPIError(
            f"Etsy API error on {method} {endpoint} ({response.status_code}): {err_message}"
        )

    if response.status_code == 204 or not response.text.strip():
        return {}

    try:
        return response.json()
    except ValueError as exc:
        raise EtsyAPIError(f"Etsy API returned non-JSON response for {endpoint}.") from exc


def get_shop_id(access_token: str) -> int:
    cached_shop_id = session.get("shop_id")
    if cached_shop_id:
        return int(cached_shop_id)

    data = etsy_request(
        "GET",
        "/application/users/__SELF__/shops",
        access_token,
        params={"limit": 100, "offset": 0},
    )

    shops = data.get("results", [])
    if not shops:
        raise EtsyAPIError("No shops found for the authenticated Etsy account.")

    shop_id = shops[0].get("shop_id")
    if not shop_id:
        raise EtsyAPIError("Unable to determine shop_id from Etsy response.")

    session["shop_id"] = int(shop_id)
    session.modified = True
    return int(shop_id)


def get_active_listings(shop_id: int, access_token: str) -> List[Dict[str, Any]]:
    listings: List[Dict[str, Any]] = []
    offset = 0
    limit = 100

    while True:
        data = etsy_request(
            "GET",
            f"/application/shops/{shop_id}/listings/active",
            access_token,
            params={"limit": limit, "offset": offset},
        )

        results = data.get("results", [])
        for item in results:
            listings.append(
                {
                    "listing_id": item.get("listing_id"),
                    "title": item.get("title", "Untitled Listing"),
                }
            )

        if len(results) < limit:
            break
        offset += limit

    return listings


def parse_listing_ids_from_request() -> List[int]:
    collected: List[str] = []

    raw_listing_ids = request.form.get("listing_ids")
    if raw_listing_ids:
        try:
            parsed = json.loads(raw_listing_ids)
            if isinstance(parsed, list):
                collected.extend(str(item) for item in parsed)
        except json.JSONDecodeError:
            collected.extend(
                item.strip() for item in raw_listing_ids.split(",") if item.strip()
            )

    collected.extend(request.form.getlist("listing_ids[]"))
    collected.extend(request.form.getlist("listing_ids"))

    unique_ids: List[int] = []
    seen = set()

    for raw in collected:
        try:
            value = int(str(raw).strip())
        except ValueError:
            continue

        if value not in seen:
            seen.add(value)
            unique_ids.append(value)

    return unique_ids


def get_listing_videos(shop_id: int, listing_id: int, access_token: str) -> List[Dict[str, Any]]:
    data = etsy_request(
        "GET",
        f"/application/shops/{shop_id}/listings/{listing_id}/videos",
        access_token,
    )
    return data.get("results", [])


def delete_listing_video(
    shop_id: int, listing_id: int, video_id: int, access_token: str
) -> None:
    etsy_request(
        "DELETE",
        f"/application/shops/{shop_id}/listings/{listing_id}/videos/{video_id}",
        access_token,
    )


def upload_listing_video(
    shop_id: int,
    listing_id: int,
    video_path: str,
    original_filename: str,
    access_token: str,
) -> None:
    with open(video_path, "rb") as video_stream:
        files = {
            "video": (original_filename, video_stream, "video/mp4"),
        }
        etsy_request(
            "POST",
            f"/application/shops/{shop_id}/listings/{listing_id}/videos",
            access_token,
            files=files,
        )


@app.route("/")
def index() -> str:
    connected = bool(session.get("access_token"))
    return render_template(
        "index.html",
        connected=connected,
        client_configured=bool(ETSY_CLIENT_ID),
    )


@app.route("/login")
def login() -> Response:
    if not ETSY_CLIENT_ID:
        return (
            jsonify(
                {
                    "error": "Missing Etsy client ID. Set ETSY_CLIENT_ID or add etsy_client_id in config.local.json.",
                }
            ),
            500,
        )

    state = secrets.token_urlsafe(24)
    code_verifier, code_challenge = generate_pkce_pair()

    session["oauth_state"] = state
    session["code_verifier"] = code_verifier
    session.modified = True

    params = {
        "response_type": "code",
        "redirect_uri": ETSY_REDIRECT_URI,
        "scope": ETSY_SCOPES,
        "client_id": ETSY_CLIENT_ID,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    authorization_url = f"{ETSY_OAUTH_URL}?{urlencode(params)}"
    return redirect(authorization_url)


@app.route("/callback")
def callback() -> Response:
    oauth_error = request.args.get("error")
    if oauth_error:
        description = request.args.get("error_description", "OAuth authorization failed.")
        return jsonify({"error": description}), 400

    state = request.args.get("state", "")
    code = request.args.get("code", "")

    if not state or state != session.get("oauth_state"):
        return jsonify({"error": "Invalid OAuth state."}), 400

    if not code:
        return jsonify({"error": "Missing authorization code in callback."}), 400

    code_verifier = session.get("code_verifier")
    if not code_verifier:
        return jsonify({"error": "Missing PKCE code_verifier in session."}), 400

    payload = {
        "grant_type": "authorization_code",
        "client_id": ETSY_CLIENT_ID,
        "redirect_uri": ETSY_REDIRECT_URI,
        "code": code,
        "code_verifier": code_verifier,
    }

    response = requests.post(ETSY_TOKEN_URL, data=payload, timeout=30)
    if response.status_code >= 400:
        return (
            jsonify(
                {
                    "error": "Failed to exchange code for token.",
                    "details": response.text,
                }
            ),
            400,
        )

    token_data = response.json()
    store_token(token_data)

    # OAuth one-time values can be dropped now.
    session.pop("oauth_state", None)
    session.pop("code_verifier", None)

    return redirect(url_for("index"))


@app.route("/api/listings", methods=["GET"])
def api_listings() -> Response:
    try:
        access_token = get_valid_access_token()
        shop_id = get_shop_id(access_token)
        listings = get_active_listings(shop_id, access_token)
        return jsonify({"shop_id": shop_id, "listings": listings})
    except EtsyAPIError as exc:
        status = 401 if "Not authenticated" in str(exc) else 400
        return jsonify({"error": str(exc)}), status
    except requests.RequestException as exc:
        return jsonify({"error": f"Network error while loading listings: {exc}"}), 502


@app.route("/api/upload", methods=["POST"])
def api_upload() -> Response:
    try:
        access_token = get_valid_access_token()
        shop_id = get_shop_id(access_token)
    except EtsyAPIError as exc:
        return jsonify({"error": str(exc)}), 401

    listing_ids = parse_listing_ids_from_request()
    if not listing_ids:
        return jsonify({"error": "No listing IDs were selected."}), 400

    video_file = request.files.get("video")
    if not video_file:
        return jsonify({"error": "No video file was uploaded."}), 400

    safe_filename = secure_filename(video_file.filename or "upload.mp4")
    if not safe_filename.lower().endswith(".mp4"):
        return jsonify({"error": "Only MP4 video files are supported."}), 400

    temp_fd, temp_path = tempfile.mkstemp(suffix=".mp4")
    os.close(temp_fd)
    video_file.save(temp_path)

    @stream_with_context
    def stream_upload_events() -> Any:
        success_count = 0
        failed_count = 0
        total = len(listing_ids)

        try:
            yield json.dumps({"type": "start", "total": total}) + "\n"

            for index, listing_id in enumerate(listing_ids, start=1):
                try:
                    iteration_access_token = get_valid_access_token()
                    existing_videos = get_listing_videos(
                        shop_id, listing_id, iteration_access_token
                    )
                    deleted_count = 0

                    for video in existing_videos:
                        video_id = video.get("video_id")
                        if video_id is None:
                            continue
                        delete_listing_video(
                            shop_id, listing_id, int(video_id), iteration_access_token
                        )
                        deleted_count += 1

                    upload_listing_video(
                        shop_id=shop_id,
                        listing_id=listing_id,
                        video_path=temp_path,
                        original_filename=safe_filename,
                        access_token=iteration_access_token,
                    )

                    success_count += 1
                    message = "Uploaded video successfully."
                    if deleted_count:
                        message = (
                            f"Deleted {deleted_count} existing video(s), then uploaded."
                        )

                    yield json.dumps(
                        {
                            "type": "listing",
                            "status": "success",
                            "index": index,
                            "total": total,
                            "listing_id": listing_id,
                            "message": message,
                        }
                    ) + "\n"
                except Exception as exc:  # Keep loop running for all selected listings.
                    failed_count += 1
                    yield json.dumps(
                        {
                            "type": "listing",
                            "status": "error",
                            "index": index,
                            "total": total,
                            "listing_id": listing_id,
                            "message": str(exc),
                        }
                    ) + "\n"

                if index < total:
                    time.sleep(UPLOAD_DELAY_SECONDS)

            yield json.dumps(
                {
                    "type": "complete",
                    "total": total,
                    "success": success_count,
                    "failed": failed_count,
                }
            ) + "\n"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    return Response(
        stream_upload_events(),
        mimetype="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=DEFAULT_PORT, debug=True)
