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

    while True:
        time.sleep(60)
