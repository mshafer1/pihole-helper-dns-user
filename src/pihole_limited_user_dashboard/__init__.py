import threading
import urllib.parse
from copy import deepcopy
from datetime import datetime, timedelta

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from cachelib import SimpleCache
from decouple import config
from flask import flash, Flask, redirect, render_template, request, session, url_for
from flask_session import Session

app = Flask(__name__)

# Configure Flask-Session
app.config["SECRET_KEY"] = config("SECRET_KEY")
app.config["SESSION_TYPE"] = "cachelib"
app.config["SESSION_CACHELIB"] = SimpleCache()
Session(app)

# Load Pi-hole settings from environment
PIHOLE_HOST = config("PIHOLE_HOST")
PIHOLE_TOKEN = config("PIHOLE_API_TOKEN")
APP_PASSWORD = config("APP_PASSWORD")


class ServerState:
    def __init__(self):
        self._lock = threading.Lock()
        self._queries = []
        self._domains = {}
        self._lists = {}
        self._backend_session = None

    @property
    def lock(self):
        return self._lock

    @property
    def queries(self):
        with self._lock:
            return deepcopy(self._queries)

    @property
    def domains(self):
        with self._lock:
            return deepcopy(self._domains)

    @property
    def lists(self):
        with self._lock:
            return deepcopy(self._lists)

    def refresh_data(self):
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
            self._queries = deepcopy(queries)
            self._lists = deepcopy(lists)
            self._domains = deepcopy(domains)

    @property
    def backend_session(self):
        with self._lock:
            return self._backend_session

    def _validate_backend_session(self, backend_session):
        response = backend_session.get(f"http://{PIHOLE_HOST}/api/lists", timeout=5)
        response.raise_for_status()
        return response.json()

    def _authenticate_backend_session(self, backend_session):
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

    def ensure_backend_session(self):
        with self._lock:
            if self._backend_session is None:
                self._backend_session = requests.Session()

            return self._authenticate_backend_session(self._backend_session)

    def refresh_backend_session(self):
        with self._lock:
            if self._backend_session is None:
                self._backend_session = requests.Session()

            return self._authenticate_backend_session(self._backend_session)

    def pihole_api_request(
        self, endpoint, params=None, method="GET", json_data=None, allowed_codes=tuple()
    ):
        """Helper utility to communicate with Pi-hole legacy/standard HTTP API"""
        if params is None:
            params = {}
        endpoint = endpoint.lstrip("/")  # Ensure no leading slash
        url = f"http://{PIHOLE_HOST}/api/{endpoint}"

        s = self.ensure_backend_session()  # Use the cached session for requests
        print(f"Pi-hole API request: {method} {url} with params {params} and json_data {json_data}")
        with self.lock:
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
                    print("Got back:", response.text)
                    result = response.json()
                else:
                    result = None
            except requests.RequestException as e:
                print(
                    f"Pi-hole API request failed: {e} - {e.response if hasattr(e, 'response') else 'No response'}"
                )
                raise Exception(f"Pi-hole API communication error: {e}")
        print(f"Pi-hole API response: {result}")
        return result


state = ServerState()

# Initialize and start APScheduler
scheduler = BackgroundScheduler()
scheduler.start()


def pihole_session_refresh():
    """Refresh the Pi-hole session by clearing the cache and re-authenticating."""
    state.refresh_backend_session()


def reblock_domain(domain):
    """Background task executed by APScheduler to re-add domain to blocklist / remove from whitelist"""
    _handle_domain_change(domain, block=True)
    print(f"APScheduler: Domain {domain} has been automatically re-blocked.")


def reunblock_domain(domain):
    """Background task executed by APScheduler to re-add domain to allowlist / remove from blocklist"""
    _handle_domain_change(domain, block=False)
    print(f"APScheduler: Domain {domain} has been automatically re-allowed.")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password")
        if password == APP_PASSWORD:
            session["logged_in"] = True
            flash("Logged in successfully.", "success")
            return redirect(url_for("home"))
        else:
            flash("Invalid password. Please try again.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))


@app.route("/")
def home():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    state.refresh_data()
    # print("Lists data:", state.lists)  # Debugging line to check the structure of ad_lists

    return render_template(
        "index.html",
        queries=state.queries[:20],
        ad_lists=list(state.lists.values()),
        domains=state.domains,
    )


def _handle_domain_change(domain, block: bool):
    """Helper function to handle domain blocking/unblocking logic."""
    if block:
        state.pihole_api_request(
            f"domains/deny/exact/{_quote_url(domain)}", method="PUT", json_data={"enabled": True}
        )
        state.pihole_api_request(
            f"domains/allow/exact/{_quote_url(domain)}",
            method="DELETE",
            allowed_codes=(200, 204, 404),
            json_data={"enabled": False, "domain": domain},
        )
    else:
        # unblocking means allowing the domain, so we remove it from the deny list and add it to the allow list
        state.pihole_api_request(
            f"domains/allow/exact/{_quote_url(domain)}", method="PUT", json_data={"enabled": True}
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
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    domain = request.form.get("domain")
    action_type = request.form.get("type")  # 'temp' or 'perm'
    next_tab = request.form.get("next_tab", "queries")

    # Always remove from whitelist initially
    _handle_domain_change(domain, block=True)

    if action_type == "temp":
        # Calculate run time (e.g., 5 minutes from now)
        run_time = datetime.now() + timedelta(minutes=5)

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
        flash(f"Domain {domain} temporarily blocked for 5 minutes.", "success")
    else:
        # If it was previously scheduled for a temp unblock, remove the job if switched to permanent
        job_id = f"reunblock_{domain}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        flash(f"Domain {domain} permanently blocked.", "success")
    result = url_for("home", _anchor=next_tab)
    print(f"Redirecting to {result}")
    return redirect(result)


@app.route("/unblock", methods=["POST"])
def unblock_domain():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    domain = request.form.get("domain")
    action_type = request.form.get("type")  # 'temp' or 'perm'
    next_tab = request.form.get("next_tab", "queries")

    # Always whitelist the domain initially
    _handle_domain_change(domain, block=False)

    if action_type == "temp":
        # Calculate run time (e.g., 5 minutes from now)
        run_time = datetime.now() + timedelta(minutes=5)

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
        flash(f"Domain {domain} temporarily unblocked for 5 minutes.", "success")
    else:
        # If it was previously scheduled for a temp unblock, remove the job if switched to permanent
        job_id = f"reblock_{domain}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        flash(f"Domain {domain} permanently allowed.", "success")

    result = url_for("home", _anchor=next_tab)
    print(f"Redirecting to {result}")
    return redirect(result)

@app.route("/toggle-list", methods=["POST"])
def toggle_list():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    list_id = request.form.get("list_id")
    enable_state = request.form.get("enable")

    list_info = state.lists.get(list_id)
    if list_info is None:
        state.refresh_data()
        list_info = state.lists.get(list_id)

    if list_info is None:
        flash(f"List ID {list_id} not found.", "danger")
        return redirect(url_for("home", anchor="lists"))
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

    flash("Domain list status updated.", "success")
    result = url_for("home", _anchor="lists")
    print(f"Redirecting to {result}")
    return redirect(result)


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
