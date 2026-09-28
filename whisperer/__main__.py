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
    sub.add_parser("serve", help="start the panel")
    a = p.parse_args()

    if a.cmd == "run":
        from whisperer.pipeline import run
        run_id = run(a.client, send_email=not a.no_email)
        print(f"run {run_id} done")
    else:
        import uvicorn
        uvicorn.run("whisperer.web:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))


if __name__ == "__main__":
    main()
