"""Provide a limited user interface for managing a Pi-hole instance.

Allows:
    - Viewing recent queries
    - Blocking and unblocking domains from queries (temporarily or permanently)
    - Viewing and toggling domain lists (enable/disable)
    - Viewing and managing blocked/allowed domains
"""

import copy
import datetime
import importlib.metadata
import threading
import urllib.parse

import apscheduler.schedulers.background
import cachelib
import decouple
import flask
import flask_cors
import flask_session
from flask_wtf.csrf import CSRFProtect
import requests

app = flask.Flask(__name__)

# Configure Flask-Session
app.config["SECRET_KEY"] = decouple.config("SECRET_KEY")
app.config["SESSION_TYPE"] = "cachelib"
app.config["SESSION_CACHELIB"] = cachelib.SimpleCache()
flask_session.Session(app)
flask_cors.CORS(app)
csrf = CSRFProtect(app)

# Load Pi-hole settings from environment
PIHOLE_HOST = decouple.config("PIHOLE_HOST")
PIHOLE_TOKEN = decouple.config("PIHOLE_API_TOKEN")
APP_PASSWORD = decouple.config("APP_PASSWORD")

DEBUG = decouple.config("DEBUG", default=False, cast=bool)


class _ServerState:
    def __init__(self):
        """Initialize the server state."""
        self._lock = threading.Lock()
        self._queries = []
        self._domains = {}
        self._lists = {}
        self._backend_session = None

    @property
    def queries(self):
        """Return a copy of the recent queries."""
        with self._lock:
            return copy.deepcopy(self._queries)

    @property
    def domains(self):
        """Return a copy of the managed domains."""
        with self._lock:
            return copy.deepcopy(self._domains)

    @property
    def lists(self):
        """Return a copy of the domain lists."""
        with self._lock:
            return copy.deepcopy(self._lists)

    def refresh_data(self):
        """Refresh the server state by fetching data from the Pi-hole API."""
        if DEBUG:
            print("Refreshing fake data...")
            self._queries = [
                {"domain": "example.com", "client": "127.0.0.1", "status": "allowed"},
            ]
            self._lists = {
                "1": {
                    "id": 1,
                    "address": "https://example.com/list1.txt",
                    "enabled": True,
                    "comment": "Example list 1",
                },
                "2": {
                    "id": 2,
                    "address": "https://example.com/list2.txt",
                    "enabled": False,
                    "comment": "Example list 2",
                },
            }
            self._domains = {
                "example.com": {"enabled": True, "type": "allow"},
                "blocked.com": {"enabled": False, "type": "deny"},
            }
            return

        queries_response = self.pihole_api_request("/queries", params={}) or {}
        queries = []
        if "queries" in queries_response:
            for entry in queries_response["queries"]:
                queries.append(
                    {
                        "domain": entry.get("domain"),
                        "client": entry.get("client"),
                        "status": entry.get("status"),
                    }
                )

        lists_response = self.pihole_api_request("lists") or {}
        lists_data = lists_response.get("lists", [])
        lists = {str(lst["id"]): lst for lst in lists_data} if lists_data else {}

        domains = {}
        domain_response = self.pihole_api_request("domains") or {}
        if "domains" in domain_response:
            for entry in domain_response["domains"]:
                domains[entry.get("domain")] = {
                    "enabled": entry.get("enabled"),
                    "type": entry.get("type"),
                }

        with self._lock:
            self._queries = copy.deepcopy(queries)
            self._lists = copy.deepcopy(lists)
            self._domains = copy.deepcopy(domains)

    def _validate_backend_session(self, backend_session):
        response = backend_session.get(f"http://{PIHOLE_HOST}/api/lists", timeout=5)
        response.raise_for_status()
        return response.json()

    def _authenticate_backend_session(self, backend_session: requests.Session):
        if DEBUG:
            print("Authenticating backend session...")
            backend_session.headers.update({"sid": "dummy_sid_for_debugging"})
            return backend_session
        if "sid" in backend_session.headers:
            print("have session, checking if still valid...")
            try:
                self._validate_backend_session(backend_session)
                return backend_session
            except Exception as e:
                print(f"Session expired or invalid, re-authenticating: {e}")
                backend_session.headers.pop("sid", None)

        response = backend_session.post(
            f"http://{PIHOLE_HOST}/api/auth", json={"password": PIHOLE_TOKEN}
        )
        response.raise_for_status()
        data = response.json()
        sid = data.get("session", {}).get("sid")
        if not sid:
            raise Exception("Failed to retrieve session ID from Pi-hole API.")
        print("logged in with backend")
        backend_session.headers.update({"sid": sid})
        return backend_session

    def _ensure_backend_session(self):
        with self._lock:
            if self._backend_session is None:
                self._backend_session = requests.Session()
                requests_version = importlib.metadata.version("requests")
                self._backend_session.headers.update(
                    {"User-Agent": f"Python-requests/{requests_version} (pihole-helper-dns-user)"}
                )

            return self._authenticate_backend_session(self._backend_session)

    def refresh_backend_session(self):
        with self._lock:
            if self._backend_session is None:
                self._backend_session = requests.Session()

            return self._authenticate_backend_session(self._backend_session)

    def pihole_api_request(
        self, endpoint, params=None, method="GET", json_data=None, allowed_codes=tuple()
    ):
        """Communicate with Pi-hole standard HTTP API."""
        if params is None:
            params = {}
        endpoint = endpoint.lstrip("/")  # Ensure no leading slash
        url = f"http://{PIHOLE_HOST}/api/{endpoint}"

        s = self._ensure_backend_session()  # Use the cached session for requests
        with self._lock:
            try:
                if method == "POST":
                    response = s.post(url, params=params, json=json_data, timeout=5)
                elif method == "PUT":
                    response = s.put(url, params=params, json=json_data, timeout=5)
                elif method == "DELETE":
                    response = s.delete(url, params=params, json=json_data, timeout=5)
                else:
                    response = s.get(url, params=params, timeout=5)
                if allowed_codes and response.status_code in allowed_codes:
                    print(f"Received allowed status code {response.status_code} for {url}")
                else:
                    response.raise_for_status()
                if response.text:
                    result = response.json()
                else:
                    result = None
            except requests.RequestException as e:
                print(
                    f"Pi-hole API request failed: {e} - {e.response if hasattr(e, 'response') else 'No response'}"
                )
                raise Exception(f"Pi-hole API communication error: {e}")
        return result


state = _ServerState()

# Initialize and start APScheduler
scheduler = apscheduler.schedulers.background.BackgroundScheduler()
scheduler.start()


def pihole_session_refresh():
    """Refresh the Pi-hole session by clearing the cache and re-authenticating."""
    state.refresh_backend_session()


def reblock_domain(domain):
    """Background scheduled task for re-blocking a domain after temporary unblocking."""
    _handle_domain_change(domain, block=True)
    print(f"APScheduler: Domain {domain} has been automatically re-blocked.")


def reunblock_domain(domain):
    """Background scheduled task for re-allowing a domain after temporary blocking."""
    _handle_domain_change(domain, block=False)
    print(f"APScheduler: Domain {domain} has been automatically re-allowed.")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Handle user login.

    If the request method is POST, validate the password and set the session.
    If GET, render the login page.
    """
    if flask.request.method == "POST":
        password = flask.request.form.get("password")
        if password == APP_PASSWORD:
            flask.session["logged_in"] = True
            flask.flash("Logged in successfully.", "success")
            return flask.redirect(flask.url_for("home"))
        else:
            flask.flash("Invalid password. Please try again.", "danger")
    return flask.render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    """Handle user logout by clearing the session and redirecting to the login page."""
    if not flask.session.get("logged_in") and not DEBUG:
        return flask.redirect(flask.url_for("login"))
    flask.session.clear()
    flask.flash("Logged out successfully.", "info")
    return flask.redirect(flask.url_for("login"))


@app.route("/")
def home():
    """Render the main dashboard page."""
    if not flask.session.get("logged_in") and not DEBUG:
        return flask.redirect(flask.url_for("login"))

    state.refresh_data()
    # print("Lists data:", state.lists)  # Debugging line to check the structure of ad_lists

    return flask.render_template(
        "index.html",
        queries=state.queries[:20],
        ad_lists=list(state.lists.values()),
        domains=state.domains,
    )


@app.route("/api/health")
def api_health():
    """Health check endpoint to verify the API is responsive."""
    try:
        state.refresh_backend_session()
    except Exception as e:
        print(f"Health check failed: {e}")
        return flask.jsonify({"status": "unhealthy", "error": str(e)}), 500
    return flask.jsonify({"status": "healthy"}), 200


def _handle_domain_change(domain, block: bool):
    """Helper function to handle domain blocking/unblocking logic."""
    if block:
        state.pihole_api_request(
            f"domains/deny/exact/{_quote_url(domain)}",
            method="PUT",
            json_data={"enabled": True},
        )
        state.pihole_api_request(
            f"domains/allow/exact/{_quote_url(domain)}",
            method="DELETE",
            allowed_codes=(200, 204, 404),
            json_data={"enabled": False, "domain": domain},
        )
    else:
        # unblocking means allowing the domain,
        # so we remove it from the deny list and add it to the allow list
        state.pihole_api_request(
            f"domains/allow/exact/{_quote_url(domain)}",
            method="PUT",
            json_data={"enabled": True},
        )
        state.pihole_api_request(
            f"domains/deny/exact/{_quote_url(domain)}",
            method="DELETE",
            allowed_codes=(200, 204, 404),
            json_data={"enabled": False, "domain": domain},
        )
    state.refresh_data()  # Refresh the state after making changes


@app.route("/block", methods=["POST"])
def block_domain():
    """Handle request to block a given domain."""
    if not flask.session.get("logged_in") and not DEBUG:
        return flask.redirect(flask.url_for("login"))

    domain = flask.request.form.get("domain")
    action_type = flask.request.form.get("type")  # 'temp' or 'perm'
    if not domain or action_type not in {"temp", "perm"}:
        flask.flash("Invalid request parameters.", "danger")
        return flask.redirect(flask.url_for("home", _anchor="domains"))

    # Always remove from whitelist initially
    _handle_domain_change(domain, block=True)

    if action_type == "temp":
        # Calculate run time (e.g., 5 minutes from now)
        run_time = datetime.datetime.now() + datetime.timedelta(minutes=5)

        # Schedule the reblock task dynamically
        # Giving the job a unique ID prevents duplicate overlapping schedules for the same domain
        scheduler.add_job(
            func=reunblock_domain,
            trigger="date",
            run_date=run_time,
            args=[domain],
            id=f"reunblock_{domain}",
            replace_existing=True,
        )
        flask.flash(f"Domain {domain} temporarily blocked for 5 minutes.", "success")
    else:
        # If it was previously scheduled for a temp unblock, remove the job if switched to permanent
        job_id = f"reunblock_{domain}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        flask.flash(f"Domain {domain} permanently blocked.", "success")
    result = flask.url_for("home", _anchor="domains")
    print(f"Redirecting to {result}")
    return flask.redirect(result)


@app.route("/unblock", methods=["POST"])
def unblock_domain():
    """Handle request to unblock a given domain."""
    if not flask.session.get("logged_in") and not DEBUG:
        return flask.redirect(flask.url_for("login"))

    domain = flask.request.form.get("domain")
    action_type = flask.request.form.get("type")  # 'temp' or 'perm'

    if not domain or action_type not in {"temp", "perm"}:
        flask.flash("Invalid request parameters.", "danger")
        return flask.redirect(flask.url_for("home", _anchor="domains"))

    # Always whitelist the domain initially
    _handle_domain_change(domain, block=False)

    if action_type == "temp":
        # Calculate run time (e.g., 5 minutes from now)
        run_time = datetime.datetime.now() + datetime.timedelta(minutes=5)

        # Schedule the reblock task dynamically
        # Giving the job a unique ID prevents duplicate overlapping schedules for the same domain
        scheduler.add_job(
            func=reblock_domain,
            trigger="date",
            run_date=run_time,
            args=[domain],
            id=f"reblock_{domain}",
            replace_existing=True,
        )
        flask.flash(f"Domain {domain} temporarily unblocked for 5 minutes.", "success")
    else:
        # If it was previously scheduled for a temp unblock, remove the job if switched to permanent
        job_id = f"reblock_{domain}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        flask.flash(f"Domain {domain} permanently allowed.", "success")

    result = flask.url_for("home", _anchor="domains")
    print(f"Redirecting to {result}")
    return flask.redirect(result)


@app.route("/toggle-list", methods=["POST"])
def toggle_list():
    """Handle request to enable or disable a domain list."""
    if not flask.session.get("logged_in") and not DEBUG:
        return flask.redirect(flask.url_for("login"))

    list_id = flask.request.form.get("list_id")
    enable_state = flask.request.form.get("enable")

    if not list_id or enable_state not in {"True", "False"}:
        flask.flash("Invalid request parameters.", "danger")
        return flask.redirect(flask.url_for("home", _anchor="lists"))

    list_info = state.lists.get(list_id)
    if list_info is None:
        state.refresh_data()
        list_info = state.lists.get(list_id)

    if list_info is None:
        flask.flash(f"List ID {list_id} not found.", "danger")
        return flask.redirect(flask.url_for("home", _anchor="lists"))
    # print("editing list", list_info)
    state.pihole_api_request(
        f"lists/{_quote_url(list_info['address'])}",
        method="PUT",
        params={"type": "block"},
        json_data={
            "enabled": True if str(enable_state) == "True" else False,
            "comment": list_info.get("comment", ""),
        },
    )

    state.refresh_data()

    flask.flash("Domain list status updated.", "success")
    result = flask.url_for("home", _anchor="lists")
    print(f"Redirecting to {result}")
    return flask.redirect(result)


def _quote_url(url):
    return urllib.parse.quote(url, safe="")


scheduler.add_job(
    func=pihole_session_refresh,
    trigger="cron",
    minute="*/5",  # todo; 20 minutes is probably better, but for testing purposes, 5 minutes is good
    args=[],
    id="pihole_session_refresh",
    replace_existing=True,
)
