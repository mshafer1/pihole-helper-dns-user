"""Use gevent to serve the Flask app for the Pi-hole helper DNS user dashboard."""

import decouple
import gevent.pywsgi

import pihole_helper_dns_user

SERVER_HOST: str = decouple.config("SERVER_HOST", default="127.0.0.1", cast=str)
SERVER_PORT: int = decouple.config("SERVER_PORT", default=5000, cast=int)


def run():
    """Run the Flask app using gevent WSGI server."""
    print(f"Serving app on interface {SERVER_HOST} (port {SERVER_PORT})")
    http_server = gevent.pywsgi.WSGIServer((SERVER_HOST, SERVER_PORT), pihole_helper_dns_user.app)
    http_server.serve_forever()


if __name__ == "__main__":
    run()
