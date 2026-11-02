"""Standalone Assignment 2.1 worker demonstration."""
import logging
import time
from pathlib import Path
from manualmate.worker import BackgroundWorker

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(filename="logs/manualmate.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(threadName)s %(message)s")
worker = BackgroundWorker(interval=1.0)
worker.start()
for name in ("scan-manual", "check-parts", "refresh-help"):
    worker.submit(name)
time.sleep(2.2)
worker.stop()
print("Worker metrics:")
for record in worker.snapshot():
    print(record)
print("Detailed timings: logs/manualmate.log")
