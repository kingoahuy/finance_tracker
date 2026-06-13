import msvcrt
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
LOG_DIR = PROJECT_ROOT / "logs"
LOCK_FILE = LOG_DIR / "service_runner.lock"
RUNNER_PID_FILE = LOG_DIR / "service_runner.pid"
STREAMLIT_PID_FILE = LOG_DIR / "streamlit.pid"
SCHEDULER_PID_FILE = LOG_DIR / "scheduler.pid"
FEISHU_BOT_PID_FILE = LOG_DIR / "feishu_bot.pid"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

sys.path.insert(0, str(PROJECT_ROOT / "finance_tracker"))
from config import EnvFileValidationError
from feishu_config import get_feishu_config


def append_runner_log(message):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with (LOG_DIR / "service_runner.log").open("a", encoding="utf-8") as log:
        log.write(f"[{timestamp}] {message}\n")


def acquire_lock():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lock_handle = LOCK_FILE.open("a+b")
    lock_handle.seek(0)
    if lock_handle.tell() == 0:
        lock_handle.write(b"0")
        lock_handle.flush()
    lock_handle.seek(0)
    try:
        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock_handle.close()
        return None
    return lock_handle


def write_pid(path, pid):
    path.write_text(str(pid), encoding="ascii")


def start_process(name, arguments):
    stdout_path = LOG_DIR / f"{name}.log"
    stderr_path = LOG_DIR / f"{name}.err.log"
    stdout_handle = stdout_path.open("a", encoding="utf-8", buffering=1)
    stderr_handle = stderr_path.open("a", encoding="utf-8", buffering=1)
    process = subprocess.Popen(
        [str(PYTHON_EXE), *arguments],
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=stdout_handle,
        stderr=stderr_handle,
        creationflags=CREATE_NO_WINDOW,
    )
    append_runner_log(f"started {name}, pid={process.pid}")
    return process, stdout_handle, stderr_handle


def close_handles(handles):
    for handle in handles:
        try:
            handle.close()
        except OSError:
            pass


def port_is_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main():
    lock_handle = acquire_lock()
    if lock_handle is None:
        return

    write_pid(RUNNER_PID_FILE, os.getpid())
    append_runner_log(f"service runner started, pid={os.getpid()}")

    streamlit = None
    scheduler = None
    feishu_bot = None
    streamlit_handles = ()
    scheduler_handles = ()
    feishu_handles = ()

    try:
        while True:
            if streamlit is None or streamlit.poll() is not None:
                close_handles(streamlit_handles)
                streamlit, *streamlit_handles = start_process(
                    "streamlit",
                    [
                        "-m",
                        "streamlit",
                        "run",
                        "finance_tracker/app.py",
                        "--server.port",
                        "8501",
                        "--server.address",
                        "127.0.0.1",
                        "--server.headless",
                        "true",
                    ],
                )
                write_pid(STREAMLIT_PID_FILE, streamlit.pid)

            if scheduler is None or scheduler.poll() is not None:
                close_handles(scheduler_handles)
                scheduler, *scheduler_handles = start_process(
                    "scheduler",
                    ["finance_tracker/scheduler.py"],
                )
                write_pid(SCHEDULER_PID_FILE, scheduler.pid)

            try:
                feishu_config = get_feishu_config()
                should_run_feishu = (
                    feishu_config.bot_enabled
                    and feishu_config.bot_ready
                    and (feishu_config.bootstrap_mode or bool(feishu_config.allowed_open_ids))
                )
            except EnvFileValidationError as exc:
                append_runner_log(f"invalid environment configuration: {exc}")
                should_run_feishu = False
            if should_run_feishu and (feishu_bot is None or feishu_bot.poll() is not None):
                close_handles(feishu_handles)
                feishu_bot, *feishu_handles = start_process(
                    "feishu_bot",
                    ["finance_tracker/feishu_bot.py"],
                )
                write_pid(FEISHU_BOT_PID_FILE, feishu_bot.pid)
            elif not should_run_feishu and feishu_bot is not None:
                feishu_bot.terminate()
                feishu_bot = None
                close_handles(feishu_handles)
                feishu_handles = ()
                FEISHU_BOT_PID_FILE.unlink(missing_ok=True)

            time.sleep(5 if port_is_open(8501) else 2)
    except Exception as exc:
        append_runner_log(f"service runner error: {exc!r}")
        raise
    finally:
        close_handles(streamlit_handles)
        close_handles(scheduler_handles)
        close_handles(feishu_handles)
        for path in (RUNNER_PID_FILE, STREAMLIT_PID_FILE, SCHEDULER_PID_FILE, FEISHU_BOT_PID_FILE):
            path.unlink(missing_ok=True)
        lock_handle.close()


if __name__ == "__main__":
    main()
