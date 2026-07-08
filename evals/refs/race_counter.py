import threading, time


class Counter:
    def __init__(self):
        self.value = 0
        self._lock = threading.Lock()

    def increment(self):
        # Lock makes the read-modify-write atomic across threads.
        with self._lock:
            v = self.value
            time.sleep(0)
            self.value = v + 1


def run(counter, n):
    for _ in range(n):
        counter.increment()
