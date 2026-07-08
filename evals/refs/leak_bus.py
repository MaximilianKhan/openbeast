class EventBus:
    """Pub/sub event bus."""
    def __init__(self):
        self._handlers = {}

    def subscribe(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)
        return handler

    def unsubscribe(self, event, handler):
        handlers = self._handlers.get(event)
        if not handlers:
            return
        try:
            handlers.remove(handler)
        except ValueError:
            pass
        if not handlers:
            del self._handlers[event]

    def publish(self, event, data):
        for h in self._handlers.get(event, []):
            h(data)


def do_work(bus, n):
    received = []
    handler = bus.subscribe("tick", lambda d: received.append(d))
    bus.publish("tick", n)
    bus.unsubscribe("tick", handler)
    return received
