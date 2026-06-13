import argparse
import base64
import datetime
import json
import subprocess
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from email_service import generate_report_content, send_email_task
from ledger import add_email_job, load_email_jobs, load_transactions
from transaction_service import create_transaction, create_transactions_from_text

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Local finance ledger operations.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_text_parser = subparsers.add_parser("add-text", help="Parse local text and add transactions.")
    add_text_parser.add_argument("text", help="Expense or income text. Use semicolons for multiple records.")
    add_text_parser.add_argument("--date", default=datetime.date.today().isoformat())
    add_text_parser.add_argument("--ensure-services", action="store_true", help="Start Streamlit and scheduler if needed before saving.")

    add_json_parser = subparsers.add_parser("add-json", help="Add transactions from a JSON list.")
    add_json_parser.add_argument("json_text", help="JSON object/list, or '-' to read JSON from stdin.")
    add_json_parser.add_argument("--base64", action="store_true", help="Decode json_text as UTF-8 Base64 JSON.")
    add_json_parser.add_argument("--ensure-services", action="store_true", help="Start Streamlit and scheduler if needed before saving.")

    recent_parser = subparsers.add_parser("recent", help="Print recent transactions as JSON.")
    recent_parser.add_argument("--limit", type=int, default=10)

    report_parser = subparsers.add_parser("report", help="Print a local Markdown report.")
    report_parser.add_argument("--date", default=datetime.date.today().isoformat())

    schedule_parser = subparsers.add_parser("schedule-report", help="Schedule a report email.")
    schedule_parser.add_argument("--report-date", default=datetime.date.today().isoformat())
    schedule_parser.add_argument("--send-at", required=True, help="YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS")

    jobs_parser = subparsers.add_parser("jobs", help="Print email jobs as JSON.")
    jobs_parser.add_argument("--status", default=None)
    jobs_parser.add_argument("--limit", type=int, default=30)

    send_parser = subparsers.add_parser("send-report", help="Send report email for a date.")
    send_parser.add_argument("--date", default=datetime.date.today().isoformat())

    subparsers.add_parser("ensure-services", help="Start Streamlit and scheduler if needed.")
    subparsers.add_parser("run-due-jobs", help="Run due pending email jobs once.")

    args = parser.parse_args()

    if args.command == "add-text":
        if args.ensure_services:
            ensure_services()
        saved = create_transactions_from_text(args.text, args.date, source="cli")
        print(json.dumps(saved, ensure_ascii=False, indent=2))
        return

    if args.command == "add-json":
        if args.ensure_services:
            ensure_services()
        json_text = sys.stdin.read() if args.json_text == "-" else args.json_text
        if args.base64:
            json_text = base64.b64decode(json_text).decode("utf-8")
        payload = json.loads(_clean_json_arg(json_text))
        records = payload if isinstance(payload, list) else [payload]
        saved = [create_transaction(record, source="cli") for record in records]
        print(json.dumps(saved, ensure_ascii=False, indent=2))
        return

    if args.command == "recent":
        df = load_transactions().head(args.limit)
        records = df.drop(columns=["_rowid"], errors="ignore").copy()
        if "date" in records.columns:
            records["date"] = pd_to_dates(records["date"])
        print(records.to_json(force_ascii=False, orient="records", indent=2))
        return

    if args.command == "report":
        target_date = datetime.datetime.strptime(args.date, "%Y-%m-%d").date()
        print(generate_report_content(load_transactions(), target_date))
        return

    if args.command == "schedule-report":
        schedule_time = _parse_datetime(args.send_at)
        job_id = add_email_job(args.report_date, schedule_time.strftime("%Y-%m-%d %H:%M:%S"))
        print(json.dumps({"id": job_id, "report_date": args.report_date, "schedule_time": schedule_time.strftime("%Y-%m-%d %H:%M:%S"), "status": "pending"}, ensure_ascii=False))
        return

    if args.command == "jobs":
        jobs = load_email_jobs(status=args.status, limit=args.limit)
        print(jobs.to_json(force_ascii=False, orient="records", indent=2))
        return

    if args.command == "send-report":
        success, message = send_email_task(args.date)
        print(json.dumps({"success": success, "message": message}, ensure_ascii=False))
        return

    if args.command == "ensure-services":
        result = ensure_services()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "run-due-jobs":
        from scheduler import check_and_run_jobs, init_scheduler_db

        init_scheduler_db()
        check_and_run_jobs()
        return


def _parse_datetime(value):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise SystemExit("--send-at must be in 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD HH:MM:SS' format")


def ensure_services():
    project_root = MODULE_DIR.parent
    script_path = project_root / "scripts" / "service_control.ps1"
    if not script_path.exists():
        return {"success": False, "message": f"service script not found: {script_path}"}

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "ensure",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.stdout:
        print(completed.stdout, file=sys.stderr)
    if completed.stderr:
        print(completed.stderr, file=sys.stderr)

    return {
        "success": completed.returncode == 0,
        "returncode": completed.returncode,
        "message": "services ensured" if completed.returncode == 0 else "service ensure failed",
    }


def _clean_json_arg(value):
    try:
        json.loads(value)
        return value
    except json.JSONDecodeError:
        return value.replace('\\"', '"')


def pd_to_dates(series):
    import pandas as pd

    return pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m-%d")


if __name__ == "__main__":
    main()
