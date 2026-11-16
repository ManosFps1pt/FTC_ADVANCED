"""Bounded browser delivery. Robot ingestion never awaits a browser socket."""
import asyncio


class BrowserBroadcast:
    def __init__(self, capacity=64, timeout=0.5):
        self.capacity = capacity
        self.timeout = timeout
        self.workers = {}
        self._cleanup_tasks = set()

    def _finished(self, task, socket, sockets):
        # A task cancelled before its first instruction never enters its finally
        # block. Cleanup belongs to its completion callback, including that case.
        sockets.discard(socket)
        worker = self.workers.get(socket)
        if worker is not None and worker[1] is task:
            self.workers.pop(socket, None)
        cleanup = asyncio.create_task(self._close_socket(socket))
        self._cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(self._cleanup_tasks.discard)

    async def _close_socket(self, socket):
        try:
            await asyncio.wait_for(socket.close(code=1013), timeout=self.timeout)
        except (Exception, asyncio.CancelledError):
            pass

    def publish(self, sockets, message):
        for socket in tuple(sockets):
            worker = self.workers.get(socket)
            if worker is None:
                queue = asyncio.Queue(self.capacity)
                task = asyncio.create_task(self._deliver(socket, sockets, queue))
                task.add_done_callback(lambda done, s=socket, group=sockets: self._finished(done, s, group))
                self.workers[socket] = (queue, task)
            else:
                queue, _ = worker
            if queue.full():
                # Disconnect rather than silently lose command responses or
                # present an incomplete event stream as complete.
                sockets.discard(socket)
                self.workers[socket][1].cancel()
                continue
            queue.put_nowait(message)

    async def _deliver(self, socket, sockets, queue):
        try:
            while socket in sockets:
                message = await queue.get()
                await asyncio.wait_for(socket.send_json(message), timeout=self.timeout)
        except (Exception, asyncio.CancelledError):
            pass

    async def close(self):
        tasks = [task for _, task in self.workers.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._cleanup_tasks:
            await asyncio.gather(*tuple(self._cleanup_tasks), return_exceptions=True)
        self.workers.clear()
