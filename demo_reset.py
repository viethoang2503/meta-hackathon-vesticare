#!/usr/bin/env python3
"""demo_reset.py — Xoa data demo, giu lai data nghien cuu.

Chay tu project root (venv phai duoc kich hoat):
    python demo_reset.py            # xoa data trong 24 gio gan nhat
    python demo_reset.py --hours 4  # xoa data trong 4 gio gan nhat
    python demo_reset.py --all      # xoa TOAN BO alerts + sensor data
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
os.chdir(os.path.join(_ROOT, "backend"))  # dotenv loads .env from cwd

from app.database import SessionLocal  # noqa: E402
from app.models.schemas import AlertEvent, SensorData  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset demo data in Vesti-Care DB")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--hours", type=float, default=None,
                       help="Xoa data trong N gio gan nhat")
    group.add_argument("--all", action="store_true",
                       help="Xoa TOAN BO sensor data va alerts")
    args = parser.parse_args()

    if args.all:
        cutoff_naive = None
        scope_desc = "TOAN BO data"
    elif args.hours:
        cutoff_naive = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).replace(tzinfo=None)
        scope_desc = f"data trong {args.hours:.0f} gio gan nhat"
    else:
        cutoff_naive = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(tzinfo=None)
        scope_desc = "data trong 24 gio gan nhat"

    db = SessionLocal()
    try:
        alert_q = db.query(AlertEvent)
        sensor_q = db.query(SensorData)
        if cutoff_naive is not None:
            alert_q = alert_q.filter(AlertEvent.timestamp >= cutoff_naive)
            sensor_q = sensor_q.filter(SensorData.timestamp >= cutoff_naive)

        n_alerts = alert_q.count()
        n_sensor = sensor_q.count()

        print(f"\n{'='*52}")
        print(f"  DEMO RESET  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Pham vi    : {scope_desc}")
        print(f"{'='*52}")
        print(f"  Se xoa: {n_alerts} alerts, {n_sensor} sensor data rows")

        if n_alerts == 0 and n_sensor == 0:
            print("\n  [OK] Khong co data can xoa. Database da sach.\n")
            return

        confirm = input("\n  Tiep tuc? [y/N] ").strip().lower()
        if confirm != "y":
            print("  Huy bo.\n")
            return

        deleted_alerts = alert_q.delete(synchronize_session=False)
        deleted_sensor = sensor_q.delete(synchronize_session=False)
        db.commit()

        print(f"\n  [OK] Da xoa {deleted_alerts} alerts")
        print(f"  [OK] Da xoa {deleted_sensor} sensor data rows")
        print(f"\n  Database san sang cho demo.\n")

    finally:
        db.close()


if __name__ == "__main__":
    main()
