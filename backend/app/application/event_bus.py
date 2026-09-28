import asyncio
from collections import defaultdict

class EventBus:
    def __init__(self): self._subs=defaultdict(set)
    def subscribe(self, project_id):
        q=asyncio.Queue(); self._subs[project_id].add(q); return q
    def unsubscribe(self, project_id, q): self._subs[project_id].discard(q)
    async def publish(self, event):
        for q in list(self._subs.get(event["project_id"], ())): await q.put(event)
