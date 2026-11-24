"""Minimal one-shot JSON-line client for the community sketchup MCP server."""
import json, socket, sys

def call(code, port=9878, timeout=60):
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "eval_ruby", "arguments": {"code": code}}}
    s = socket.create_connection(("127.0.0.1", port), 10)
    s.sendall(json.dumps(msg).encode() + b"\n")
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    return buf.decode("utf-8", "replace").strip()

if __name__ == "__main__":
    print(call(sys.argv[1] if len(sys.argv) > 1 else "Sketchup.version"))
