"""MCP 一键客户端 — 调 NagiBridge MCP 工具（streamable-http + SSE），省得每次手搓握手。

用法（先 cd scripts 或给绝对路径）:
    python mcp_cli.py list                              # 列全部工具名
    python mcp_cli.py map '{"ops":"go","kw":{"destination":"BusStop"}}'
    python mcp_cli.py check '{"kw":{"what":"status"}}'
    python mcp_cli.py farm  '{"ops":"collect"}'

⚠️ 关键坑（2026-08-31 恒让我写下来，省得每次重新发现）:
  - 服务器 http://localhost:8000/mcp，**streamable-http**（不是 SSE 裸流，需 initialize 拿 session）。
  - 请求 Accept 头**必须同时**声明 `application/json` AND `text/event-stream`，否则 400。
  - 域工具签名 `def map(ops: str, **kw)`，FastMCP 会把 **kw 包成 `{"kw": {...}}`，服务器里 `_ops_run` 会解包 → **`kw` 必须传 JSON 对象**，如 `"kw":{"destination":"BusStop"}`（写字符串会接到空参数）。
  - 传参时 args 直接给工具入参 JSON（ops=动作, kw={"key":值}）。
"""
import sys, json, os, requests

BASE = os.environ.get("NAGI_MCP_URL", "http://localhost:8000/mcp")
HDR = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "._mcp_session")


def _parse(resp):
    # ⚠️ 服务器 SSE(streamable-http) 返回 text/event-stream 无 charset → requests 按 HTTP 规范默认
    #    Latin-1 解码 → UTF-8 中文读成乱码(双重编码)。必须用 resp.content(bytes) 强制 utf-8 解码，
    #    否则 mcp_cli 调试输出全乱(dict/json 路径同理，resp.json() 也走 resp.text 的 Latin-1)。
    body = resp.content.decode("utf-8", errors="replace")
    ct = resp.headers.get("Content-Type", "")
    if "event-stream" in ct:
        for block in body.split("\n\n"):
            for line in block.split("\n"):
                if line.startswith("data:"):
                    try:
                        return json.loads(line[5:].strip())
                    except Exception:
                        pass
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def _get_session():
    sess = requests.Session()
    sess.headers.update(HDR)
    sid = None
    if os.path.exists(SESSION_FILE):
        try:
            sid = open(SESSION_FILE, encoding="utf-8").read().strip()
        except Exception:
            sid = None
    if sid:
        sess.headers["Mcp-Session-Id"] = sid
        # 试复用：连续 session 不初始化（服务器可能拒绝旧 session，失败再重建）
        return sess, sid
    r = sess.post(BASE, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                         "clientInfo": {"name": "mcp_cli", "version": "1"}}},
                  headers=HDR, timeout=10)
    sid = r.headers.get("Mcp-Session-Id")
    if sid:
        sess.headers["Mcp-Session-Id"] = sid
        open(SESSION_FILE, "w", encoding="utf-8").write(sid)
        sess.post(BASE, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=HDR, timeout=10)
    return sess, sid


def call(method, params, sess):
    r = sess.post(BASE, json={"jsonrpc": "2.0", "id": 9, "method": method, "params": params},
                  headers=HDR, timeout=60)
    return _parse(r)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"
    sess, sid = _get_session()
    if mode == "list":
        data = call("tools/list", {}, sess)
        tools = (data or {}).get("result", {}).get("tools", [])
        print(f"MCP {BASE} · session={sid} · 工具数 {len(tools)}")
        for t in tools:
            desc = (t.get("description") or "").split("\n")[0][:70]
            print(f"  - {t.get('name')}: {desc}")
        return
    name = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    res = call("tools/call", {"name": name, "arguments": args}, sess)
    if not res:
        print("❌ 无响应"); return
    if res.get("error"):
        print("err:", json.dumps(res["error"], ensure_ascii=False)); return
    content = (res.get("result") or {}).get("content") or []
    for c in content:
        if c.get("type") == "text":
            print(c.get("text") or "")
        else:
            print(json.dumps(c, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()
