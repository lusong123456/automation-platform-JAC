"""执行互斥：平台同一时刻只允许一次执行（20 条用例量级，不排队）。"""

import threading

_lock = threading.Lock()


def try_acquire() -> bool:
    return _lock.acquire(blocking=False)


def release() -> None:
    if _lock.locked():
        _lock.release()


def is_running() -> bool:
    return _lock.locked()
