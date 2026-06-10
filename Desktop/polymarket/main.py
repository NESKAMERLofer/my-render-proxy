#!/usr/bin/env python3
"""Entry point."""
import sys

if __name__ == "__main__":
    # Optional CLI: python main.py wallet 0x1234...
    if len(sys.argv) >= 3 and sys.argv[1] == "wallet":
        import database, analyzer
        database.init_db()
        print(analyzer.format_wallet_report(sys.argv[2]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "stats":
        import database, analyzer
        database.init_db()
        print(analyzer.format_global_report())
        print(analyzer.format_top_report())
    else:
        from bot import run
        run()
