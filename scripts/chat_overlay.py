"""
Stardew Valley Chat Overlay — 跟随游戏窗口的聊天气泡，只显示最新一条

启动:
    python chat_overlay.py [--port 7850]

发消息:
    POST http://localhost:7850/message  {"sender":"AI","text":"hello"}
"""

import argparse
import ctypes
import json
import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.request
from dataclasses import dataclass, field
from http.server import HTTPServer, BaseHTTPRequestHandler

import win32gui
import win32con

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

TRANSPARENT_KEY = "#010101"

COLORS = {
    "bubble_bg":     "#3a2a1a",
    "bubble_border": "#8b7355",
    "sender_nagi":   "#7eb8da",
    "sender_rinai":  "#f0a0c0",
    "sender_system": "#90EE90",
    "text":          "#FFF8DC",
}

BUBBLE_PAD_X = 12
BUBBLE_PAD_Y = 8
BUBBLE_MAX_WIDTH = 320
CORNER_MARGIN = 16


@dataclass
class ChatBubble:
    sender: str
    text: str
    canvas_ids: list = field(default_factory=list)
    height: int = 0


def find_game_window():
    result = [None]
    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            cls = win32gui.GetClassName(hwnd)
            if cls == "SDL_app":
                result[0] = hwnd
                return False
        return True
    try:
        win32gui.EnumWindows(callback, None)
    except Exception:
        pass
    return result[0]


class OverlayHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/message":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8", errors="replace"))
            self.server.msg_queue.put(body)
            self._respond({"ok": True})
        elif self.path == "/clear":
            self.server.msg_queue.put({"action": "clear"})
            self._respond({"ok": True})
        else:
            self._respond({"error": "not found"}, 404)

    def do_GET(self):
        if self.path == "/health":
            self._respond({"status": "ok"})
        elif self.path == "/inbox":
            overlay = self.server.overlay
            msgs = list(overlay.inbox)
            overlay.inbox.clear()
            self._respond({"ok": True, "messages": msgs})
        else:
            self._respond({"error": "not found"}, 404)

    def _respond(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, *args):
        pass


class ChatOverlay:
    def __init__(self, port=7850):
        self.port = port
        self.msg_queue = queue.Queue()
        self.bubble = None
        self.game_hwnd = None
        self.game_rect = None
        self.visible = False

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
        self.root.config(bg=TRANSPARENT_KEY)

        self.canvas = tk.Canvas(
            self.root, bg=TRANSPARENT_KEY, highlightthickness=0,
            width=BUBBLE_MAX_WIDTH + CORNER_MARGIN * 2,
            height=200,
        )
        self.canvas.pack(fill=tk.X)

        try:
            self.font_sender = tkfont.Font(family="FixedSys", size=11, weight="bold")
            self.font_text = tkfont.Font(family="FixedSys", size=11)
            self.font_input = tkfont.Font(family="FixedSys", size=11)
        except Exception:
            self.font_sender = tkfont.Font(family="Consolas", size=10, weight="bold")
            self.font_text = tkfont.Font(family="Consolas", size=10)
            self.font_input = tkfont.Font(family="Consolas", size=10)

        self.input_frame = tk.Frame(self.root, bg=COLORS["bubble_bg"], padx=4, pady=4)
        self.input_frame.pack(fill=tk.X, padx=CORNER_MARGIN, pady=(0, CORNER_MARGIN))

        self.input_var = tk.StringVar()
        self.input_entry = tk.Entry(
            self.input_frame, textvariable=self.input_var,
            font=self.font_input, bg="#1a1008", fg=COLORS["text"],
            insertbackground=COLORS["text"], relief=tk.FLAT,
            borderwidth=0,
        )
        self.input_entry.pack(fill=tk.X, ipady=4)
        self.input_entry.bind("<Return>", self._on_send)

        self.inbox = []

        self.root.update_idletasks()

    CHANNEL_URL = "http://127.0.0.1:9000"
    OUTBOX_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chat_outbox.txt")
    INBOX_PATH = os.path.join(os.path.expanduser("~"), "nagi", "overlay_inbox.jsonl")

    def _on_send(self, event=None):
        text = self.input_var.get().strip()
        if not text:
            return
        self.input_var.set("")
        self.show_message("Player", text)
        threading.Thread(target=self._send_message, args=(text,), daemon=True).start()

    def _send_message(self, text):
        """写入inbox文件（hook读取），同时尝试POST channel server"""
        try:
            os.makedirs(os.path.dirname(self.INBOX_PATH), exist_ok=True)
            with open(self.INBOX_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({"text": text, "ts": time.time()}, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[inbox] write failed: {e}", flush=True)
        try:
            data = json.dumps({"message": text, "chat_id": "game1"}).encode()
            req = urllib.request.Request(
                self.CHANNEL_URL,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass

    def _poll_outbox(self):
        """轮询outbox文件，有新回复就显示气泡"""
        last_ts = 0
        while True:
            try:
                if os.path.exists(self.OUTBOX_PATH):
                    with open(self.OUTBOX_PATH, "r", encoding="utf-8") as f:
                        data = json.loads(f.read())
                    ts = data.get("ts", 0)
                    if ts > last_ts:
                        last_ts = ts
                        text = data.get("text", "")
                        if text:
                            self.root.after(0, lambda t=text: self.show_message("AI", t))
            except Exception:
                pass
            time.sleep(1)

    def _sender_color(self, sender):
        s = sender.lower()
        if "AI" in s or "nagi" in s:
            return COLORS["sender_nagi"]
        if "Player" in s or "rina" in s:
            return COLORS["sender_rinai"]
        if "system" in s:
            return COLORS["sender_system"]
        return "#FFD700"

    def _wrap_text(self, text, max_width):
        lines = []
        current = ""
        for ch in text:
            if ch == "\n":
                lines.append(current)
                current = ""
                continue
            test = current + ch
            if self.font_text.measure(test) > max_width:
                if current:
                    lines.append(current)
                current = ch
            else:
                current = test
        if current:
            lines.append(current)
        return lines or [""]

    def show_message(self, sender, text):
        self.canvas.delete("all")

        lines = self._wrap_text(text, BUBBLE_MAX_WIDTH - BUBBLE_PAD_X * 2)
        line_height = self.font_text.metrics("linespace")
        sender_height = self.font_sender.metrics("linespace") if sender else 0

        content_h = BUBBLE_PAD_Y * 2 + len(lines) * line_height
        if sender:
            content_h += sender_height + 4

        bx = CORNER_MARGIN
        by = CORNER_MARGIN

        self.canvas.create_rectangle(
            bx, by, bx + BUBBLE_MAX_WIDTH, by + content_h,
            fill=COLORS["bubble_bg"], outline=COLORS["bubble_border"], width=2
        )

        ty = by + BUBBLE_PAD_Y
        if sender:
            self.canvas.create_text(
                bx + BUBBLE_PAD_X, ty, text=sender, anchor="nw",
                font=self.font_sender, fill=self._sender_color(sender)
            )
            ty += sender_height + 4

        for line in lines:
            self.canvas.create_text(
                bx + BUBBLE_PAD_X, ty, text=line, anchor="nw",
                font=self.font_text, fill=COLORS["text"]
            )
            ty += line_height

        canvas_h = content_h + CORNER_MARGIN * 2
        canvas_w = BUBBLE_MAX_WIDTH + CORNER_MARGIN * 2
        self.canvas.config(width=canvas_w, height=canvas_h)
        self.root.update_idletasks()
        input_h = self.input_frame.winfo_reqheight() + CORNER_MARGIN
        total_h = canvas_h + input_h
        self.root.geometry(f"{canvas_w}x{total_h}")
        self._position_overlay()

    def _position_overlay(self):
        if not self.game_rect:
            return
        gl, gt, gr, gb = self.game_rect
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = gl
        y = gb - h
        self.root.geometry(f"+{x}+{y}")

    def track_game_window(self):
        if self.game_hwnd and not win32gui.IsWindow(self.game_hwnd):
            self.game_hwnd = None

        if not self.game_hwnd:
            self.game_hwnd = find_game_window()

        if self.game_hwnd:
            try:
                placement = win32gui.GetWindowPlacement(self.game_hwnd)
                show_cmd = placement[1]

                if show_cmd == win32con.SW_SHOWMINIMIZED:
                    if self.visible:
                        self.root.withdraw()
                        self.visible = False
                else:
                    rect = win32gui.GetWindowRect(self.game_hwnd)
                    if rect != self.game_rect:
                        self.game_rect = rect
                        self._position_overlay()

                    if not self.visible:
                        self.root.deiconify()
                        self.visible = True
            except Exception:
                self.game_hwnd = None
        else:
            if self.visible:
                self.root.withdraw()
                self.visible = False

        self.root.after(200, self.track_game_window)

    def process_queue(self):
        while not self.msg_queue.empty():
            try:
                msg = self.msg_queue.get_nowait()
                if isinstance(msg, dict):
                    if msg.get("action") == "clear":
                        self.canvas.delete("all")
                    elif msg.get("text"):
                        self.show_message(
                            msg.get("sender", ""),
                            msg.get("text", "")
                        )
            except queue.Empty:
                break
        self.root.after(50, self.process_queue)

    def run(self):
        server = HTTPServer(("127.0.0.1", self.port), OverlayHandler)
        server.msg_queue = self.msg_queue
        server.overlay = self
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"Chat overlay running on port {self.port}", flush=True)

        threading.Thread(target=self._poll_outbox, daemon=True).start()
        print(f"Outbox polling: {self.OUTBOX_PATH}", flush=True)

        self.root.after(100, self.track_game_window)
        self.root.after(50, self.process_queue)
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7850)
    args = parser.parse_args()
    overlay = ChatOverlay(port=args.port)
    overlay.run()


if __name__ == "__main__":
    main()
