import asyncio
from collections import deque


class AdjustableLimiter:
    """FIFO semaphore whose limit can change without replacing pending waiters."""

    def __init__(self, limit):
        self._limit = self._validate(limit)
        self._active = 0
        self._waiters = deque()

    @staticmethod
    def _validate(limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("concurrency limit must be a positive integer")
        return limit

    def resize(self, limit):
        self._limit = self._validate(limit)
        self._wake()

    def locked(self):
        return self._active >= self._limit or bool(self._waiters)

    def _wake(self):
        while self._waiters and self._active < self._limit:
            waiter = self._waiters.popleft()
            if not waiter.done():
                self._active += 1
                waiter.set_result(True)

    async def acquire(self):
        if not self.locked():
            self._active += 1
            return True
        waiter = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            return await waiter
        except asyncio.CancelledError:
            if waiter.done() and not waiter.cancelled():
                self._active -= 1
            else:
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    pass
            self._wake()
            raise

    def release(self):
        self._active -= 1
        self._wake()

    async def __aenter__(self):
        await self.acquire()
        return None

    async def __aexit__(self, *exc):
        self.release()
