import functools
import urllib.parse
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

# global var, yuck
_lists = {}

# Initialize and start APScheduler
scheduler = BackgroundScheduler()
scheduler.start()


def _auth_session(s: requests.Session):
    """Authenticate the session with Pi-hole API and store the session ID in headers."""
    if "sid" in s.headers:
        print("have session, checking if still valid...")
        # extend auth by doing an authenticated action
        try:
            pihole_api_request("lists")
            return
        except Exception as e:
            print(f"Session expired or invalid, re-authenticating: {e}")
            s.headers.pop("sid", None)  # Remove the old session ID

    r = s.post(f"http://{PIHOLE_HOST}/api/auth", json={"password": PIHOLE_TOKEN})
    r.raise_for_status()  # Raise an error if authentication fails
    data = r.json()
    sid = data.get("session", {}).get("sid")
    if not sid:
        raise Exception("Failed to retrieve session ID from Pi-hole API.")
    print("logged in with backend")
    s.headers.update({"sid": sid})  # Add the session ID to the session headers


@functools.lru_cache(maxsize=1)
def pihole_session():
    """Return the current session object, cached for performance."""
    s = requests.Session()
    _auth_session(s)  # Authenticate the session
    return s


def pihole_session_refresh():
    """Refresh the Pi-hole session by clearing the cache and re-authenticating."""
    s = pihole_session()  # Get the current session
    _auth_session(s)  # Re-authenticate the session


def pihole_api_request(endpoint, params=None, method="GET", json_data=None):
    """Helper utility to communicate with Pi-hole legacy/standard HTTP API"""
    if params is None:
        params = {}
    endpoint = endpoint.lstrip("/")  # Ensure no leading slash
    url = f"http://{PIHOLE_HOST}/api/{endpoint}"

    s = pihole_session()  # Use the cached session for requests
    print(f"Pi-hole API request: {method} {url} with params {params} and json_data {json_data}")
    try:
        if method == "POST":
            response = s.post(url, params=params, json=json_data, timeout=5)
        elif method == "PUT":
            response = s.put(url, params=params, json=json_data, timeout=5)
        else:
            response = s.get(url, params=params, timeout=5)
        response.raise_for_status()
        result = response.json()
    except Exception as e:
        print(
            f"Pi-hole API request failed: {e} - {e.response.text if hasattr(e, 'response') else 'No response'}"
        )
        raise Exception(f"Pi-hole API communication error: {e}") from None
    # print(f"Pi-hole API response: {result}")
    return result


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
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid password. Please try again.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))


@app.route("/")
def dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    data = pihole_api_request("/queries", params={})
    # data = []
    recent_queries = []
    if data and "queries" in data:
        for entry in data["queries"]:
            recent_queries.append(
                {
                    "domain": entry.get("domain"),
                    "client": entry.get("client"),
                    "status": entry.get("status"),
                }
            )

    lists_data = pihole_api_request("lists").get("lists", [])
    _lists.clear()  # Clear the global _lists variable
    _lists.update({str(lst["id"]): lst for lst in lists_data} if lists_data else {})
    print("Lists data:", _lists)  # Debugging line to check the structure of ad_lists

    return render_template("index.html", queries=recent_queries[:20], ad_lists=lists_data)


def _handle_domain_change(domain, block: bool):
    """Helper function to handle domain blocking/unblocking logic."""
    pihole_api_request("domains/block/exact", params={"domain": domain, "enabled": block})
    pihole_api_request("domains/allow/exact", params={"domain": domain, "enabled": not block})


@app.route("/block", methods=["POST"])
def block_domain():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    domain = request.form.get("domain")
    action_type = request.form.get("type")  # 'temp' or 'perm'

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
    return redirect(url_for("dashboard"))


@app.route("/unblock", methods=["POST"])
def unblock_domain():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    domain = request.form.get("domain")
    action_type = request.form.get("type")  # 'temp' or 'perm'

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

    return redirect(url_for("dashboard"))


@app.route("/toggle-list", methods=["POST"])
def toggle_list():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    list_id = request.form.get("list_id")
    enable_state = request.form.get("enable")

    try:
        list_info = _lists[list_id]
    except KeyError:
        flash(f"List ID {list_id} not found.", "danger")
        return redirect(url_for("dashboard"))
    print("editing list", list_info)
    pihole_api_request(
        f"lists/{urllib.parse.quote(list_info['address'], safe='')}",
        method="PUT",
        params={"type": "block"},
        json_data={
            "enabled": True if str(enable_state) == "True" else False,
            "comment": list_info.get("comment", ""),
        },
    )

    flash("Domain list status updated.", "success")
    return redirect(url_for("dashboard"))


scheduler.add_job(
    func=pihole_session_refresh,
    trigger="cron",
    minute="*/5",  # todo; 20 minutes is probably better, but for testing purposes, 5 minutes is good
    args=[],
    id="pihole_session_refresh",
    replace_existing=True,
)
