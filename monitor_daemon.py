#!/usr/bin/env python3
"""
monitor_daemon.py — Background safety loops as a STANDALONE process.

Runs auto_scheduler (9:10 start / 15:30 stop), webhook_monitor_loop (trailing
SL/target/3:15 squareoff for webhook positions) and pos_monitor_loop (SL/TP/
3:15 EOD-squareoff + RMS daily-loss breaker for every open position) — pulled
out of trader_dashboard.py's in-process threads into their own process/systemd
service (algo-monitor) so that restarting/redeploying the dashboard (UI
tweaks, route fixes, etc.) never pauses live SL/TP/squareoff coverage even
for the few seconds a `systemctl restart algo-dashboard` takes.

Imports trader_dashboard as a plain module — this only defines functions /
builds the Flask `app` object (no port binding happens until app.run(), which
is gated behind `if __name__ == '__main__'` there), so it's safe to reuse the
exact same loop code with zero duplication/drift risk.

Run: python monitor_daemon.py
"""
import threading
import time

import trader_dashboard as td

if __name__ == '__main__':
    print("\n🛡️  Monitor Daemon — auto_scheduler / webhook_monitor / pos_monitor", flush=True)
    print("   (independent of algo-dashboard — dashboard restarts don't touch this)\n", flush=True)

    threading.Thread(target=td.auto_scheduler, daemon=True).start()
    threading.Thread(target=td.webhook_monitor_loop, daemon=True).start()
    threading.Thread(target=td.pos_monitor_loop, daemon=True).start()
    # Price-trigger watcher (~1s, cache-only spot) — NIFTY level cross → option
    # order via execution_gateway. Lives here (same process as the poller that
    # keeps index spot warm) so it reads spot with zero extra Dhan calls.
    threading.Thread(target=td.trigger_watch_loop, daemon=True).start()

    # Option-chain "unusual behaviour" watcher (~60s — reads the chain snapshots
    # the /curves page uses, fires bell/toast alerts on gamma spike / straddle
    # crush / fast OI unwind). `on_fire` hook lets the auto-straddle feature (C)
    # place a PAPER straddle when a straddle-move/gamma alert fires.
    try:
        import option_alerts
        threading.Thread(target=lambda: option_alerts.watch_loop(on_fire=td.on_option_alert),
                         daemon=True).start()
    except Exception as _e:
        print(f"[monitor] option_alerts not started: {_e}", flush=True)

    # Auto ATM straddle (short) — ~3s loop: 9:20 scheduled fire (A) + combined-
    # premium 30/30 basket exit for every open auto-straddle. Lives here (same
    # process as the poller that keeps both legs' LTP warm). All PAPER.
    threading.Thread(target=td.auto_straddle_loop, daemon=True).start()

    # Positional hedged short-strangle + roll + IV-gate (~3s loop). OFF by default
    # (config._auto_strangle.enabled_920=false), PAPER hard-locked. Self-contained in
    # _ops/strangle_live.py — no-op unless enabled or a manual fire is open.
    try:
        import strangle_live
        threading.Thread(target=strangle_live.strangle_loop, daemon=True).start()
    except Exception as _e:
        print(f"[monitor] strangle_live not started: {_e}", flush=True)

    # Weekly positional iron-fly (02.17, ~3s loop). OFF by default
    # (config._weekly_ironfly.enabled=false), PAPER hard-locked. Self-contained in
    # _ops/weekly_ironfly_live.py — no-op unless enabled or a manual fire is open.
    try:
        import weekly_ironfly_live
        threading.Thread(target=weekly_ironfly_live.ironfly_loop, daemon=True).start()
    except Exception as _e:
        print(f"[monitor] weekly_ironfly_live not started: {_e}", flush=True)

    # IV-pop M-rollover iron-fly (02.18, ~20s loop). OFF by default
    # (config._m_pattern_ironfly.enabled=false), PAPER hard-locked. Self-contained in
    # _ops/m_pattern_ironfly_live.py — no-op unless enabled.
    try:
        import m_pattern_ironfly_live
        threading.Thread(target=m_pattern_ironfly_live.mpfly_loop, daemon=True).start()
    except Exception as _e:
        print(f"[monitor] m_pattern_ironfly_live not started: {_e}", flush=True)

    # StockMock-style (_sm) config strategies — generic scheduled leg-basket runner.
    # On a qualifying day at entry time, fires the configured legs once; exits are the
    # existing pos_monitor's job (per-leg SL% tag + EOD). PAPER. See _ops/sm_runner.py.
    threading.Thread(target=td.sm_runner_loop, daemon=True).start()

    # P7: one batched Dhan LTP call per cycle covers all open positions +
    # index spot, fanned out via shared_ltp_cache (pos_monitor / webhook /
    # risk_gate / _rest_ltp_fallback all read that cache first). This process
    # OWNS account-wide LTP polling — everything else only makes one-off
    # cache-miss calls.
    import ltp_poller
    ltp_poller.start()

    while True:
        time.sleep(60)
