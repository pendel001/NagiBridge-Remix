"""
Chat Watcher — 轮询 overlay_inbox.jsonl，有新消息就用 pyautogui 打进 CC 终端

启动:
    python chat_watcher.py [--interval 2] [--window-title "Claude"]

流程:
    overlay 写 inbox → watcher 检测 → 激活终端 → 打字+回车 → CC 收到触发 hook
"""

import argparse
import json
import os
import time

import pyautogui
import win32gui
import win32con


INBOX_PATH = os.path.expanduser("~/nagi/overlay_inbox.jsonl")

pyautogui.PAUSE = 0.05


def inbox_has_messages():
    """只检查有没有新消息，不清空——留给hook去读"""
    if not os.path.exists(INBOX_PATH):
        return False
    try:
        return os.path.getsize(INBOX_PATH) > 0
    except OSError:
        return False


def find_cc_window():
    """找CC终端：Windows Terminal窗口，排除Stardew相关的"""
    results = []
    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            cls = win32gui.GetClassName(hwnd)
            if cls == "CASCADIA_HOSTING_WINDOW_CLASS":
                title = win32gui.GetWindowText(hwnd)
                if "Stardew" not in title and "SMAPI" not in title:
                    results.append(hwnd)
        return True
    win32gui.EnumWindows(callback, None)
    return results[0] if results else None


def send_to_terminal(hwnd):
    """Alt trick强制切前台 + pyautogui打字"""
    import ctypes
    user32 = ctypes.windll.user32
    KEYEVENTF_KEYUP = 0x0002
    VK_MENU = 0x12
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.3)
    pyautogui.typewrite("ok", interval=0.05)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.1)
    pyautogui.press("enter")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    print(f"Chat watcher started. Polling {INBOX_PATH} every {args.interval}s", flush=True)

    while True:
        try:
            if inbox_has_messages():
                hwnd = find_cc_window()
                if hwnd:
                    title = win32gui.GetWindowText(hwnd)
                    print(f"[{time.strftime('%H:%M:%S')}] new msg, poking: {title[:40]}", flush=True)
                    send_to_terminal(hwnd)
                else:
                    print(f"[{time.strftime('%H:%M:%S')}] new msg, CC terminal not found", flush=True)
        except Exception as e:
            print(f"[error] {e}", flush=True)

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
