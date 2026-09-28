import asyncio
from collections import defaultdict


class EventBus:
    def __init__(self, queue_size: int = 128):
        self._subs = defaultdict(set)
        self.queue_size = queue_size

    def subscribe(self, project_id):
        queue = asyncio.Queue(maxsize=self.queue_size)
        self._subs[project_id].add(queue)
        return queue

    def unsubscribe(self, project_id, q):
        subscribers = self._subs.get(project_id)
        if subscribers is None:
            return
        subscribers.discard(q)
        if not subscribers:
            self._subs.pop(project_id, None)

    async def publish(self, event):
        # A slow browser must never hold up a pipeline stage.  Events are
        # notifications; REST/job.json remains the source of truth, so when a
        # queue is full it is safe to discard its oldest notification.
        for queue in list(self._subs.get(event["project_id"], ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass
