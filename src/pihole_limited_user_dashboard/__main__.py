from gevent.pywsgi import WSGIServer

import pihole_limited_user_dashboard


def run():
    http_server = WSGIServer(("0.0.0.0", 5000), pihole_limited_user_dashboard.app)
    http_server.serve_forever()


if __name__ == "__main__":
    run()
