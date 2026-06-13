import time
import sqlite3
import datetime
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parent

if sys.stdout is None or sys.stderr is None:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = (log_dir / "scheduler.log").open("a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = (log_dir / "scheduler.err.log").open("a", encoding="utf-8", buffering=1)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

# 确保 email_service.py 在同一目录下
from email_service import send_email_task
from ledger import connect, init_db


def init_scheduler_db():
    """
    初始化数据库：确保 email_jobs 表存在。
    这样即使不运行 app.py，直接运行调度器也不会报错。
    """
    init_db()
    print("✅ 数据库检查完毕：email_jobs 表已就绪。")


def check_and_run_jobs():
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 正在检查定时任务...")

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with connect() as conn:
            jobs = conn.execute(
                "SELECT id, report_date FROM email_jobs WHERE status='pending' AND schedule_time <= ?",
                (now_str,),
            ).fetchall()
    except sqlite3.OperationalError as e:
        print(f"⚠️ 查询出错: {e}")
        return

    for job in jobs:
        job_id, report_date = job
        print(f"🚀 触发任务 ID:{job_id}, 报告日期:{report_date}")

        # 2. 执行发送 (调用 email_service)
        try:
            success, msg = send_email_task(report_date)
        except Exception as e:
            success = False
            msg = str(e)
            print(f"❌ 发送过程抛出异常: {e}")

        # 3. 更新数据库状态
        new_status = 'sent' if success else 'failed'
        # 也可以记录错误信息，这里为了简单只更新状态
        with connect() as conn:
            conn.execute("UPDATE email_jobs SET status = ? WHERE id = ?", (new_status, job_id))
        print(f"✅ 任务 ID:{job_id} 处理结束，结果: {new_status} ({msg})")

    try:
        from bitable_sync import auto_sync_enabled, sync_pending_transactions

        if auto_sync_enabled():
            result = sync_pending_transactions(limit=50)
            if result["processed"]:
                print(f"飞书多维表格同步：成功 {result['succeeded']}，失败 {result['failed']}。")
    except Exception as e:
        print(f"⚠️ 飞书多维表格重试失败: {e}")


if __name__ == "__main__":
    print("🔥 定时任务调度器启动中...")

    # 1. 先初始化数据库，防止报错 "no such table"
    init_scheduler_db()

    print("🚀 调度器开始运行 (按 Ctrl+C 停止)...")
    while True:
        try:
            check_and_run_jobs()
            # 每 60 秒检查一次
            time.sleep(60)
        except KeyboardInterrupt:
            print("\n🛑 用户停止了调度器。")
            break
        except Exception as e:
            print(f"❌ 发生未知错误: {e}")
            time.sleep(60)
