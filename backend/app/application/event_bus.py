import asyncio
from collections import defaultdict

class EventBus:
    def __init__(self): self._subs=defaultdict(set)
    def subscribe(self, project_id):
        q=asyncio.Queue(); self._subs[project_id].add(q); return q
    def unsubscribe(self, project_id, q):
        subscribers = self._subs.get(project_id)
        if subscribers is None:
            return
        subscribers.discard(q)
        if not subscribers:
            self._subs.pop(project_id, None)
    async def publish(self, event):
        for q in list(self._subs.get(event["project_id"], ())): await q.put(event)
