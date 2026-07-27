import http.server
import socketserver

PORT = 8000

class MyHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # This is a local dev/test server - always serve the latest files
        # on disk. Without this, browsers cache viewer.js/index.html
        # independently of the page URL (a query-string cache-bust on the
        # page doesn't affect the module script's own cache entry), so a
        # local rebuild can silently keep testing stale, previously-loaded
        # code with no visible indication anything is out of date.
        self.send_header('Cache-Control', 'no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

# Map .mjs explicitly to application/javascript
MyHandler.extensions_map['.mjs'] = 'application/javascript'

with socketserver.TCPServer(("", PORT), MyHandler) as httpd:
    print(f"Serving at http://localhost:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
