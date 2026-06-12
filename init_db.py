"""Initialize the database with empty tables.

Run this once after cloning the repo:
    python init_db.py
"""
import sys
from pathlib import Path

# Ensure the finance_tracker package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent / "finance_tracker"))

from ledger import init_db

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")