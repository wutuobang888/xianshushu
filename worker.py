"""独立定时任务进程；Web 进程不得同时开启调度器。"""
import signal
import threading

import config

if __name__ == "__main__":
    if not config.SCHEDULER_ENABLED or config.DEMO_MODE:
        raise SystemExit("正式任务进程需要 XIANSHUSHU_SCHEDULER=1、DEMO_MODE=0")
    from services.process_lock import acquire
    worker_lock = acquire(config.DATA_DIR / "locks" / "worker.lock","另一个定时任务进程已在运行")
    from app import app
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
