"""
Channel server — 监听 localhost:9000，接收游戏聊天消息，写入 inbox 文件
"""
import json, os, time
from http.server import HTTPServer, BaseHTTPRequestHandler

INBOX = os.path.join(os.path.expanduser("~"), "nagi", "overlay_inbox.jsonl")
os.makedirs(os.path.dirname(INBOX), exist_ok=True)

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        msg = body.get("message", "")
        if msg:
            entry = {"text": msg, "ts": time.time()}
            with open(INBOX, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            print(f"[收到] {msg}", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode())

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"listening"}')

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 9000), Handler)
    print(f"Channel server listening on :9000, inbox: {INBOX}", flush=True)
    server.serve_forever()
