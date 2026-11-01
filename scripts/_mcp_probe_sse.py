# -*- coding: utf-8 -*-
"""极简 MCP 客户端（raw HTTP + SSE），绕开 mcp SDK 在本机的 502 问题。
用法（在本文件里改 CALLS 即可）。"""
import io, json, sys, requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

URL = "http://127.0.0.1:8000/mcp"
H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _post(payload, sess=None, timeout=180):
    h = dict(H)
    if sess:
        h["Mcp-Session-Id"] = sess
    r = requests.post(URL, json=payload, headers=h, timeout=timeout)
    msgs = []
    for line in r.text.splitlines():
        if line.startswith("data:"):
            try:
                msgs.append(json.loads(line[5:].strip()))
            except Exception:
                pass
    return r, msgs


def open_session():
    r, msgs = _post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "probe", "version": "0"}}})
    sess = r.headers.get("mcp-session-id")
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sess=sess)
    return sess


def call(sess, name, args, rid):
    r, msgs = _post({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                     "params": {"name": name, "arguments": args}}, sess=sess)
    if r.status_code != 200:
        return f"HTTP {r.status_code}: {r.text[:200]}"
    for m in msgs:
        if m.get("id") == rid:
            res = m.get("result") or {}
            parts = [c.get("text", "") for c in (res.get("content") or [])]
            return "".join(parts) if parts else json.dumps(res, ensure_ascii=False)[:600]
    return f"（无匹配响应）{r.text[:300]}"


CALLS = [
    ("map", {"ops": "query", "kw": {"function": "Museum"}}),
    ("menu", {"ops": "cancel", "kw": {}}),
]

sess = open_session()
print("session:", sess)
for i, (name, args) in enumerate(CALLS, start=2):
    print("=" * 64)
    print(name, json.dumps(args, ensure_ascii=False))
    print(call(sess, name, args, i)[:1200])
