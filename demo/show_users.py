#!/usr/bin/env python3
"""Print rows from the users table (same DB as app.py). Run: python show_users.py"""
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "app.db")


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, name, email FROM users ORDER BY id").fetchall()
    conn.close()
    if not rows:
        print("users: (empty)")
        return
    print(f"{'id':>6}  {'name':<24}  email")
    print("-" * 60)
    for r in rows:
        print(f"{r['id']:>6}  {r['name']:<24}  {r['email']}")
    print(f"\nTotal: {len(rows)} user(s)")


if __name__ == "__main__":
    main()
