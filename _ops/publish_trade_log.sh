#!/usr/bin/env bash
# publish_trade_log.sh — daily snapshot → PRIVATE repo arsalanali36/algo-trade-log
#
# Kyun: phone/claude.ai Project is repo ko GitHub connector se padhta hai, taaki
# "aaj strategy X ne kya kiya" jaise sawaal asli data pe answer hon (random nahi).
# Public algo-trader repo pe trade data KABHI nahi (memory: project_code3b_trade_log_repo).
#
# Kya jaata hai (context-size ke hisaab se trimmed):
#   TRADE_LOG.md + trades.csv          export_trade_log.py (order_store netting, Rule 6B)
#   logs_today/<strategy>.log          aaj touch hui har log ka last 400 lines
#   state/*.json                       health_report, downloader_alert, heartbeat, registry
#   state/notifications_recent.jsonl   last 300 notifications
#   reports/eod_<date>.html            last 10 EOD reports
#
# Run: systemd algo-tradelog.timer (Mon-Fri 15:58 IST). DRY=1 → git steps skip.
set -euo pipefail

PROJ="/root/ARSALAN/CODE3B- TV BACKTEST ENGINE"
REPO="${REPO:-/root/ARSALAN/algo-trade-log}"
PY="$PROJ/venv/bin/python"
TODAY=$(TZ=Asia/Kolkata date +%F)
NOW=$(TZ=Asia/Kolkata date +%H:%M)
DRY="${DRY:-0}"

if [ "$DRY" != "1" ]; then
  [ -d "$REPO/.git" ] || { echo "🔴 repo missing at $REPO — clone karo (deploy key add hua?)"; exit 2; }
  git -C "$REPO" pull --ff-only -q || echo "⚠️ pull failed, continuing with local"
fi
mkdir -p "$REPO/logs_today" "$REPO/state" "$REPO/reports"

cd "$PROJ"
"$PY" -X utf8 _ops/export_trade_log.py --out "$REPO"

# --- aaj ki logs (format mixed: kuch me date nahi, isliye mtime se chuno) ---
rm -f "$REPO/logs_today"/*.log
while IFS= read -r f; do
  b=$(basename "$f")
  tail -n 400 "$f" > "$REPO/logs_today/$b" || true
  [ -s "$REPO/logs_today/$b" ] || rm -f "$REPO/logs_today/$b"
done < <(find logs -maxdepth 1 -name '*.log' -newermt "$TODAY 00:00" 2>/dev/null)

# --- state ---
for s in data/health_report.json data/downloader_alert.json data/heartbeat_state.json strategy_registry.json; do
  [ -f "$s" ] && cp -f "$s" "$REPO/state/" || true
done
[ -f data/notifications.jsonl ] && tail -n 300 data/notifications.jsonl > "$REPO/state/notifications_recent.jsonl" || true

# --- reports (last 10) ---
[ -f "data/reports/eod_$TODAY.html" ] && cp -f "data/reports/eod_$TODAY.html" "$REPO/reports/" || true
ls -t "$REPO/reports"/eod_*.html 2>/dev/null | tail -n +11 | xargs -r rm -f

# --- README = Claude ke liye map ---
cat > "$REPO/README.md" <<EOF
# algo-trade-log (PRIVATE) — auto snapshot
Last update: **$TODAY $NOW IST** (VPS timer, Mon-Fri ~15:58)

Ye repo CODE3B algo-trader (NSE options, Dhan/Kite) ka **daily read-only snapshot** hai,
taaki Claude (phone/web) project-specific sawaalon ka jawab asli data se de.

| File | Kya hai |
|------|---------|
| \`TRADE_LOG.md\` | Day-by-day trades + per-strategy / per-mode (paper/live) / all-time summary. P&L = GROSS (pts × qty). |
| \`trades.csv\` | Har completed trade flat (programmatic). |
| \`logs_today/<strategy>.log\` | Aaj active har strategy ki log ka last 400 lines. Strategy ids → naam \`state/strategy_registry.json\` me. |
| \`state/health_report.json\` | 09:20 preflight (token / heartbeat / LTP / ATM resolve). |
| \`state/downloader_alert.json\` | Dashboard red-banner alerts. |
| \`state/heartbeat_state.json\` | Dead-man switch state. |
| \`state/notifications_recent.jsonl\` | Last 300 bell/Telegram notifications. |
| \`reports/eod_<date>.html\` | EOD report (last 10 din). |

Code + LESSONS.md + ARCHITECTURE_LOG.md public repo \`arsalanali36/algo-trader\` me hain.
Registry ids (02.17 weekly iron-fly, 04.03.02 chainzone, etc.) usi repo ke \`strategy_registry.json\` se.
EOF

if [ "$DRY" = "1" ]; then echo "DRY: export done → $REPO"; exit 0; fi

cd "$REPO"
git add -A
if git diff --cached --quiet; then echo "no changes $TODAY"; exit 0; fi
git commit -q -m "auto: $TODAY $NOW snapshot"
git push -q origin HEAD
echo "✅ published $TODAY $NOW"
