"""
🛏️ 睡觉/一起睡 回归检查脚本（2026-08-14）

复现 8/14 验证锁定的成功流程（transcript 1885 行配方）：
    warp 进小屋(GUID) → 同地图 walk 到床边 → crawl_bed sleep(不挪位)
    → /sleep stay(就地 ready) → 等过夜 → 核对醒来位置

用法:
    PYTHONIOENCODING=utf-8 python3 sleep_check.py [--who 名字] [--scenario own|host]

参数:
    --who         睡谁的床（名字）；默认按场景自动决定
    --scenario    own=睡自己小屋床（1885 基线） / host=一起睡（轮回爬恒床，需恒配合确认）
    --no-detect   跳过端口自动检测（默认自动检测并打印映射）

输出:
    每步 ✅/❌ + 汇总 + 醒来位置核对，落盘 scripts/sleep_check.log
"""
import sys
import os
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import stardew_api as api

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sleep_check.log")


def log_line(msg):
    print(msg, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def step_logger(step, ok, detail):
    log_line(f"    {'✅' if ok else '❌'} {step}: {detail}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--who", default="", help="睡谁的床（名字），默认按场景自动")
    parser.add_argument("--scenario", default="own", choices=["own", "host"],
                        help="own=睡自己小屋床 / host=一起睡(轮回爬恒床)")
    parser.add_argument("--no-detect", action="store_true", help="跳过端口自动检测")
    args = parser.parse_args()

    try:
        open(LOG_PATH, "w", encoding="utf-8").close()  # 每次运行清空旧日志
    except Exception:
        pass
    log_line("🛏️ sleep_check 开始  scenario=" + args.scenario)

    # 0. 角色检测（端口↔角色按启动顺序分配，重启可能翻转）
    r = {"ok": False}
    if not args.no_detect:
        r = api.ensure_roles(ttl=0)  # ttl=0 强制重新探测
        if r.get("ok"):
            a, h = r["ai"], r["host"]
            log_line(f"🔌 角色映射: AI({a.get('name','?')})={a['port']} | host({h.get('name','?')})={h['port']}")
        else:
            log_line(f"⚠️ 角色检测未完成: {r.get('error')}")
            log_line(f"   当前 AI_BASE_URL={api.AI_BASE_URL} host={api.HOST_URL}")
            log_line("   （游戏未开？请先启动游戏；或端口不是 7842/7843 用 NAGI_URL 指定）")

    # 1. 决定 who
    who = args.who
    if not who:
        if args.scenario == "own":
            # 轮回自己：先取检测结果里的 AI 角色名；失败再取 /crawl_bed locate 的 player2
            if r.get("ok"):
                who = r["ai"].get("name", "")
            if not who:
                try:
                    loc = api._ai_post("/crawl_bed", {"action": "locate"})
                    who = loc.get("player2", "")
                except Exception:
                    who = ""
        # else: host 场景 → who="" 默认睡房主(MasterPlayer)的床

    if args.scenario == "host":
        log_line("👥 一起睡：轮回会爬到恒的床并 ready。")
        log_line("   请【恒】现在就去 FarmHouse 自己的床，轮回就绪后你上床确认睡觉，天就过。")
        log_line("   ⏱️ 轮回就绪后有约 20 秒窗口；超时它会自动起身重爬一次（正常现象，别慌，继续睡即可）。")
    elif who:
        log_line(f"🛏️ 睡自己床：轮回 → 自己小屋床（{who}）。")
        log_line("   请【恒】现在去 FarmHouse 自己的床睡觉（各自睡各自床），天就过。")
        log_line("   ⏱️ 轮回就绪后有约 20 秒窗口；超时它会自动起身重爬一次（正常现象，别慌，继续睡即可）。")
    else:
        log_line("🛏️ 睡自己床：名字未取到，将按房主床处理（可能是单人档）。")

    # 2. 跑共享流程（stardew_api.go_sleep_flow — 单一真源，MCP go_sleep 也是它）
    log_line("── 开始睡觉流程 ──")
    res = api.go_sleep_flow(who, log=step_logger)

    # 3. 报告
    log_line("── 结果 ──")
    log_line(f"  {'✅' if res.get('ok') else '❌'} 汇总: {res.get('summary', '?')}")
    for s in res.get("steps", []):
        if s["step"] in ("locate", "reach_bed", "crawl", "ready", "night", "wake_check"):
            log_line(f"    {'✅' if s['ok'] else '❌'} {s['step']}: {s['detail']}")
    if res.get("woke_in_expected_bed") is not None:
        w = res.get("wake") or {}
        mark = "✅" if res.get("woke_in_expected_bed") else "❌"
        log_line(f"  {mark} 醒来位置核对: {w.get('loc')}({w.get('x')},{w.get('y')})")
    if res.get("co_sleep") and res.get("woke_in_expected_bed") is True:
        log_line("  🌹 一起睡彩蛋成功！在对方床上醒来。")
    log_line("── sleep_check 结束，日志见 " + LOG_PATH + " ──")

    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
