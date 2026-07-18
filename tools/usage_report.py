"""Local usage counter — reads transcriptions.db and prints a usage summary.

Whisper is billed by AUDIO DURATION (not tokens): whisper-large-v3-turbo is
~$0.04 per hour of audio on Groq. This tool sums the audio you've transcribed
(total + this month), broken down by source (mic vs system-audio vs command),
and estimates the Groq cost.

Run:  venv\\Scripts\\python.exe tools\\usage_report.py

Note: in BYOK mode transcription goes straight to YOUR Groq account, so the
authoritative bill is at https://console.groq.com/usage — this is a local,
offline convenience counter based on what the app saved.
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH  # noqa: E402

# Groq pricing (USD). Audio = per hour; keep in one place so it's easy to update.
WHISPER_USD_PER_HOUR = 0.04


def _fmt_hms(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def main():
    if not os.path.exists(DB_PATH):
        print(f"No hay base de datos todavía en {DB_PATH}")
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    total = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(duration_seconds),0) secs FROM transcriptions"
    ).fetchone()
    month = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(duration_seconds),0) secs "
        "FROM transcriptions WHERE created_at >= date('now','start of month')"
    ).fetchone()
    by_model = conn.execute(
        "SELECT COALESCE(model,'?') model, COUNT(*) n, "
        "COALESCE(SUM(duration_seconds),0) secs "
        "FROM transcriptions GROUP BY model ORDER BY secs DESC"
    ).fetchall()
    conn.close()

    total_hours = total["secs"] / 3600
    month_hours = month["secs"] / 3600

    print("=" * 56)
    print("  KeyLess by Sinsajo - contador de uso (local)")
    print("=" * 56)
    print(f"  TOTAL:    {total['n']:>5} dictados | {_fmt_hms(total['secs']):>12}")
    print(f"            ~ ${total_hours * WHISPER_USD_PER_HOUR:0.3f} en Groq (audio)")
    print(f"  ESTE MES: {month['n']:>5} dictados | {_fmt_hms(month['secs']):>12}")
    print(f"            ~ ${month_hours * WHISPER_USD_PER_HOUR:0.3f} en Groq (audio)")
    print("-" * 56)
    print("  Por fuente:")
    for r in by_model:
        print(f"    {r['model']:<28} {r['n']:>4} | {_fmt_hms(r['secs'])}")
    print("=" * 56)
    print("  Nota: costo estimado (audio x $0.04/h). En modo BYOK la")
    print("  factura real esta en https://console.groq.com/usage")
    print("=" * 56)


if __name__ == "__main__":
    main()
