import decouple
from gevent.pywsgi import WSGIServer

import pihole_limited_user_dashboard

SERVER_HOST: str = decouple.config("SERVER_HOST", default="127.0.0.1", cast=str)
SERVER_PORT: int = decouple.config("SERVER_PORT", default=5000, cast=int)


def run():
    http_server = WSGIServer((SERVER_HOST, SERVER_PORT), pihole_limited_user_dashboard.app)
    http_server.serve_forever()


if __name__ == "__main__":
    run()
