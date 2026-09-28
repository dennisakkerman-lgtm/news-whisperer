"""CLI: python -m whisperer run [--client basicfit] [--no-email] | serve"""
from __future__ import annotations

import argparse
import logging
import os


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="whisperer")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the weekly pipeline once")
    r.add_argument("--client")
    r.add_argument("--no-email", action="store_true")
    r.add_argument("--test", action="store_true", help="sample data for sources without credentials; no e-mail unless --email")
    r.add_argument("--email", action="store_true", help="with --test: also send the [TEST] e-mail")
    sub.add_parser("serve", help="start the panel")
    a = p.parse_args()

    if a.cmd == "run":
        from whisperer.pipeline import run
        send = a.email if a.test else not a.no_email
        run_id = run(a.client, send_email=send, test=a.test or os.environ.get("WHISPERER_TEST_MODE") == "1")
        print(f"run {run_id} done")
    else:
        import uvicorn
        uvicorn.run("whisperer.web:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))


if __name__ == "__main__":
    main()
