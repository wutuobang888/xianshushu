"""跨进程互斥使用孔夫子登录目录；人工登录或填单期间暂停其他浏览器任务。"""
from pathlib import Path
from services.process_lock import acquire


class BrowserBusyError(RuntimeError):
    pass


class LockedContext:
    def __init__(self, context, lock):
        self._context = context
        self._lock = lock

    def __getattr__(self, name):
        return getattr(self._context, name)

    def close(self):
        try:
            self._context.close()
        finally:
            self._lock.close()


def launch_persistent(browser_type, profile, **options):
    directory = Path(profile)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        lock = acquire(directory.parent / (directory.name + ".lock"),
                       "孔夫子窗口正在登录或填单，关闭该窗口后后台检查自动恢复")
    except RuntimeError as error:
        raise BrowserBusyError(str(error)) from None
    try:
        context = browser_type.launch_persistent_context(user_data_dir=str(directory), **options)
    except Exception:
        lock.close()
        raise
    return LockedContext(context, lock)
