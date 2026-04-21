"""Simple HTTP server that serves tests/plush.jpg for fetch_image tool testing."""

import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

IMAGE_PATH = Path(__file__).resolve().parent.parent / "tests" / "plush.jpg"


class ImageHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/image"):
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(IMAGE_PATH.stat().st_size))
            self.end_headers()
            self.wfile.write(IMAGE_PATH.read_bytes())
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {format % args}")


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080

    if not IMAGE_PATH.exists():
        print(f"Error: {IMAGE_PATH} not found")
        sys.exit(1)

    server = HTTPServer((host, port), ImageHandler)
    print(f"Serving {IMAGE_PATH.name} at http://{host}:{port}/image")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
