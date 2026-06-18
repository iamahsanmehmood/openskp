import http.server
import socketserver

PORT = 8000

class MyHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # Allow CORS if needed, but not strictly required
        super().end_headers()

# Map .mjs explicitly to application/javascript
MyHandler.extensions_map['.mjs'] = 'application/javascript'

with socketserver.TCPServer(("", PORT), MyHandler) as httpd:
    print(f"Serving at http://localhost:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
