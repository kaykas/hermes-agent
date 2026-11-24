#!/usr/bin/env python3
"""Fresh-process probe for the fleet broker Browser Use adapter."""
import argparse
from tools.browser_use_cli import browser_exec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', required=True)
    parser.add_argument('--url', default='https://example.com')
    args = parser.parse_args()
    code = f"new_tab({args.url!r})\nwait_for_load()\nprint(page_info())\nprint(js('document.title'))"
    print(browser_exec(code, session=args.session, timeout_s=90, task_id='fleet-broker-adapter-probe'))


if __name__ == '__main__':
    main()
