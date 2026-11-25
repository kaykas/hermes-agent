#!/usr/bin/env python3
"""Attach Hermes vault filling to a fleet-broker browser session."""
import argparse
import json
import os
from pathlib import Path
import sqlite3

from tools.browser_supervisor import SUPERVISOR_REGISTRY
from tools.browser_session_cdp import get_dialog_policy_config, resolve_cdp_override
from tools.browser_vault_tool import browser_vault_enter_code, browser_vault_fill


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', required=True)
    parser.add_argument('--handle', required=True)
    parser.add_argument('--code', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ.get('FLEET_BROWSER_HOME', '/Users/jkw/.hermes/browser-broker'))
    con = sqlite3.connect(f'file:{root / "broker.db"}?mode=ro', uri=True)
    row = con.execute("SELECT port,status FROM sessions WHERE id=?", (args.session,)).fetchone()
    con.close()
    if not row or row[1] != 'active' or not row[0]:
        print(json.dumps({'success': False, 'error': 'active broker session required'}))
        raise SystemExit(3)
    policy, timeout_s = get_dialog_policy_config()
    SUPERVISOR_REGISTRY.get_or_start(task_id=args.session,
        cdp_url=resolve_cdp_override(f'http://127.0.0.1:{row[0]}'),
        dialog_policy=policy, dialog_timeout_s=timeout_s)
    raw = browser_vault_enter_code(args.handle, task_id=args.session) if args.code else browser_vault_fill(args.handle, task_id=args.session)
    print(raw)


if __name__ == '__main__':
    main()
