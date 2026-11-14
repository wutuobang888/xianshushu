"""操作期间持有跨进程文件锁，进程退出时操作系统自动释放；不删除锁文件。"""
import os
from pathlib import Path


def acquire(path, message):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    lock = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            if not lock.read(1):
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise RuntimeError(message) from None
    return lock
