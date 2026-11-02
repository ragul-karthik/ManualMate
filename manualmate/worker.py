"""Thread-safe background job queue and periodic latency monitor."""
from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Callable, Any

logger = logging.getLogger("manualmate")


@dataclass
class Job:
    name: str
    action: Callable[[], Any]
    future: Future


class BackgroundWorker:
    def __init__(self, interval: float = 2.0):
        self.interval = interval
        self.jobs: queue.Queue[Job | None] = queue.Queue()
        self._lock = threading.Lock()
        self._metrics: list[dict[str, Any]] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="manualmate-worker", daemon=True)
        self._thread.start()
        logger.info("worker started interval=%.2fs", self.interval)

    def submit(self, name: str, action: Callable[[], Any] | None = None) -> Future:
        future = Future()
        self.jobs.put(Job(name, action or (lambda: {"status": "ok", "task": name}), future))
        logger.info("job queued name=%s depth=%d", name, self.jobs.qsize())
        return future

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._metrics)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self.jobs.put(None)
        if self._thread:
            self._thread.join(timeout)
        logger.info("worker stopped")

    def _run(self) -> None:
        next_monitor = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_monitor:
                started = time.perf_counter()
                time.sleep(0.005)  # simulated local manual/deal monitor poll
                elapsed_ms = (time.perf_counter() - started) * 1000
                metric = {"kind": "monitor", "latency_ms": round(elapsed_ms, 3), "at": time.strftime("%H:%M:%S")}
                with self._lock:
                    self._metrics.append(metric)
                    self._metrics[:] = self._metrics[-100:]
                logger.info("monitor poll latency_ms=%.3f", elapsed_ms)
                next_monitor = time.monotonic() + self.interval
            timeout = max(0.01, min(0.1, next_monitor - time.monotonic()))
            try:
                job = self.jobs.get(timeout=timeout)
            except queue.Empty:
                continue
            if job is None:
                self.jobs.task_done()
                continue
            started = time.perf_counter()
            try:
                result = job.action()
                job.future.set_result(result)
                elapsed_ms = (time.perf_counter() - started) * 1000
                record = {"kind": "job", "name": job.name, "latency_ms": round(elapsed_ms, 3), "status": "completed", "at": time.strftime("%H:%M:%S")}
                logger.info("job complete name=%s latency_ms=%.3f", job.name, elapsed_ms)
            except Exception as exc:  # keep daemon alive after a failed job
                job.future.set_exception(exc)
                elapsed_ms = (time.perf_counter() - started) * 1000
                record = {"kind": "job", "name": job.name, "latency_ms": round(elapsed_ms, 3), "error": str(exc), "at": time.strftime("%H:%M:%S")}
                logger.exception("job failed name=%s", job.name)
            with self._lock:
                self._metrics.append(record)
                self._metrics[:] = self._metrics[-100:]
            self.jobs.task_done()
