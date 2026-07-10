#!/usr/bin/env python3
"""
set_password.py — set / change the dashboard login.

Usage:
    python set_password.py <username> <password>
    python set_password.py arsalan "MyStrongPass123"

Writes a hashed password to data/auth.json (gitignored). Run again anytime to
change it. Restart of the dashboard is NOT required — the login reads the file
live on each attempt (only the secret_key is cached, and that never changes).
"""
import sys
import _paths  # noqa: F401 — puts _core/ on sys.path
import dashboard_auth as auth


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    username, password = sys.argv[1], sys.argv[2]
    try:
        auth.set_credentials(username, password)
    except ValueError as e:
        print(f"✗ {e}")
        sys.exit(1)
    print(f"✓ Login set for user '{username}'. File: data/auth.json")
    print("  Sign in at the dashboard with these credentials.")


if __name__ == "__main__":
    main()
