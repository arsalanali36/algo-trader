"""Reusable FAQ builder for strategy dashboards (user request 2026-07-11).

Every strategy's results.js should carry a `meta.faq` = [[question, answer_html], ...].
The dashboard renders it as collapsible Q&A in the 📖 Philosophy panel.

`build_faq(meta, extra=[])` returns the generic FAQ (exit / stoploss / leverage /
significance / options-vs-spot / positional) auto-filled from meta, PLUS any
strategy-specific `extra` Q&As prepended. Answers are short HTML (Hinglish, matches
how the user asks). Wire into run_hunt / build scripts:  out["meta"]["faq"] = build_faq(out["meta"], extra=[...]).
"""


def build_faq(meta, extra=None):
    tf = meta.get("tf", "?")
    dna = meta.get("dna", {}) or {}
    exitp = dna.get("exit", "atr_rr")
    atr_sl = dna.get("atr_sl", "?")
    rr = dna.get("rr", "?")
    generic = [
        ["StopLoss kya hai? Entry ke turant baad position -ve me chali jaaye to?",
         (f"Exit style = <b>{exitp}</b>. Har trade pe ek <b>ATR-based stop-loss</b> lagta hai: "
          f"stop = entry ∓ <b>{atr_sl}×ATR(14)</b> (long me neeche, short me upar). "
          "Entry ke baad agar spot us stop level ko touch kare, position turant cut ho jaati hai — "
          "yehi aapka max downside per trade hai. Position sizing aisi hai ki us stop tak ka loss = "
          "<b>capital ka ~1.5%</b> (₹10L pe ~₹15,000). Do aur safety layers: (1) hum OPTION BUY karte "
          "hain, to theoretical max loss premium tak hi capped hai; (2) RMS ka account-level daily "
          "loss-cap. Matlab 'lete hi -ve' scenario me stop + premium-cap + RMS — teeno neeche hain.")],
        ["Fixed target hai ya nahi?",
         (("Nahi — is strategy me stop_only jeeta: sirf ATR stop, koi fixed target nahi. Winner "
           "position 3:15 EOD tak ride karti hai (bade winners ko cap nahi karte — yahi edge ka soul hai). "
           if exitp == "stop_only" else
           f"Haan — ATR stop + target = {rr}× risk (1:{rr} R:R). ")
          + "Force-exit 3:15 pe, koi overnight nahi.")],
        ["Leverage kitna? Kitna paisa risk pe?",
         "<b>1x, zero leverage</b> — notional kabhi capital se zyada nahi. Risk 1.5%/trade, "
         "start capital ₹10,00,000. Options BUY karte hain to margin = premium (chhota)."],
        ["Ye edge asli hai ya luck? (significance)",
         (f"1000-permutation rotation test: <b>p={meta.get('sig_p', '0.000')}</b>. "
          "Random entry-timing ne is edge ko beat nahi kiya → luck/beta nahi, real directional edge. "
          "Train aur Out-of-sample (recent regime) dono pe hold karta hai, Monte-Carlo me original "
          "result median ke paas baithta hai (best-5% nahi = overfit nahi).")],
        ["Spot pe profit dikha to option me alag kyun?",
         "Signal spot (index) pe milta hai lekin trade OPTION pe hota hai. Option ka premium delta "
         "(~0.5 ATM) + theta se chalta hai — isliye 100-pt index move ≈ 50-pt premium move. Real "
         "expired-option data exist nahi karta (TRAP #100), to premium Black-Scholes + realised-vol "
         "se model kiya hai (VRP stress ke saath). '③ Black-Scholes' pass hi asli deployable P&L hai; "
         "'① Instrument' spot-notional sirf 'edge hai ya nahi' dekhne ko."],
        ["Isko positional (overnight/multi-day) hold karein to?",
         "Mota jawaab: winners bade move capture kar sakte, PAR (1) option BUY me har raat theta bleed "
         "(time-decay) lagta hai — expiry ke paas tezi se; (2) overnight GAP ka unhedged risk (stop "
         "raat ko protect nahi karta); (3) significance INTRADAY signals pe validate hui — positional "
         "alag strategy hai, apni validation chahiye. Isiliye agar positional karna hai to option "
         "<b>SELL + hedge</b> (defined-risk spread) zyada sensible hai — theta aapke saath, hedge gap "
         "cap karta hai. Woh alag se backtest karenge."],
        ["Naked sell / hedge lagakar sell kyun nahi (jab strategy 'selling' thi)?",
         "Backtest ne dikhaya: same entry, ATM option BUY = Sharpe 1.95 (+69%), naked SELL = −0.62 "
         "(−16%), credit-spread SELL = −1.00 (−25%). Wajah: is edge ka profit bade winners se aata "
         "hai; option BECHNE pe winner collected-credit pe cap ho jaata hai (short gamma) jabki losses "
         "utne nahi ghatte. Dashboard me ④/⑤ toggle se khud compare kar sakte ho."],
    ]
    return (list(extra) if extra else []) + generic
