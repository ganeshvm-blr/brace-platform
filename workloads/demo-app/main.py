from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import sys

PORT = int(os.environ.get("PORT", "8080"))


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","service":"brace-demo-workload"}\n')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        sys.stdout.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), format % args))
        sys.stdout.flush()


def run() -> None:
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    sys.stdout.write(f"Starting public demo HTTP workload on port {PORT}...\n")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
