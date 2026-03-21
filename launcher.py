"""
Permafrost Launcher — Start all daemons (brain, scheduler, watchdog, channels).

Usage:
    python launcher.py                    # Start all services
    python launcher.py --config config.json  # With custom config
    python launcher.py --status           # Check service status
    python launcher.py --stop             # Stop all services
"""

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.brain import PFBrain
from core.scheduler import PFScheduler
from core.watchdog import PFWatchdog
from core.guard import PFContextGuard
from core.notifier import PFNotifier
from smart.night_silence import PFNightSilence

# Channel imports (triggers registration)
import channels.telegram  # noqa: F401
import channels.discord   # noqa: F401
import channels.web       # noqa: F401
from channels.base import create_channel

log = logging.getLogger("permafrost.launcher")


def load_config(path: str = None) -> dict:
    """Load config from file or default location."""
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    default = Path(os.path.expanduser("~/.permafrost/config.json"))
    if default.exists():
        with open(default, "r", encoding="utf-8") as f:
            return json.load(f)

    return {}


def check_status(data_dir: str = None):
    """Print status of all services."""
    from datetime import datetime
    dd = Path(data_dir or os.path.expanduser("~/.permafrost"))

    services = [
        ("Brain", dd / "brain-heartbeat.json"),
        ("Scheduler", dd / "scheduler-heartbeat.json"),
    ]

    for name, hb_file in services:
        if hb_file.exists():
            try:
                hb = json.loads(hb_file.read_text(encoding="utf-8"))
                ts = datetime.fromisoformat(hb["timestamp"])
                age = (datetime.now() - ts).total_seconds()
                status = "ONLINE" if age < 180 else f"STALE ({age:.0f}s)"
                print(f"  {name}: {status} (PID {hb.get('pid', '?')})")
            except Exception:
                print(f"  {name}: ERROR reading heartbeat")
        else:
            print(f"  {name}: NOT RUNNING")


def stop_services(data_dir: str = None):
    """Stop all services by reading PID files."""
    dd = Path(data_dir or os.path.expanduser("~/.permafrost"))
    pid_file = dd / "brain.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if sys.platform == "win32":
                os.system(f"taskkill /PID {pid} /F >nul 2>&1")
            else:
                os.kill(pid, signal.SIGTERM)
            print(f"  Stopped PID {pid}")
            pid_file.unlink()
        except Exception as e:
            print(f"  Error stopping: {e}")
    else:
        print("  No PID file found")


def launch(config: dict):
    """Launch all Permafrost services."""
    data_dir = config.get("data_dir", os.path.expanduser("~/.permafrost"))
    Path(data_dir).mkdir(parents=True, exist_ok=True)

    threads = []

    # 1. Brain
    brain = PFBrain()
    brain.config.update(config)
    brain.data_dir = Path(data_dir)

    # 2. Register channels
    active_channels = []
    for ch_name in ["web", "telegram", "discord"]:
        key = f"{ch_name}_enabled"
        if config.get(key, ch_name == "web"):
            try:
                ch = create_channel(ch_name, config=config, data_dir=data_dir)
                ok, err = ch.validate()
                if ok:
                    brain.register_channel(ch_name, str(ch.inbox_file), ch.reply_handler)
                    active_channels.append((ch_name, ch))
                    log.info(f"channel {ch_name}: enabled")
                else:
                    log.warning(f"channel {ch_name}: {err}")
            except Exception as e:
                log.warning(f"channel {ch_name}: {e}")

    # 3. Night silence
    silence = PFNightSilence(data_dir=data_dir, config=config)

    # 4. Notifier
    notifier = PFNotifier(config=config, data_dir=data_dir)
    notifier.set_night_silence(silence)
    for ch_name, ch in active_channels:
        notifier.register_channel(ch_name, ch.send_message)

    # 5. Scheduler
    scheduler = PFScheduler(data_dir=data_dir, config=config)

    # 6. Watchdog
    watchdog = PFWatchdog(data_dir=data_dir, config=config)
    watchdog.register_service("brain",
                              str(Path(data_dir) / "brain-heartbeat.json"),
                              [sys.executable, "-m", "core.brain"])
    watchdog.register_service("scheduler",
                              str(Path(data_dir) / "scheduler-heartbeat.json"),
                              [sys.executable, "-m", "core.scheduler"])

    # 7. Context Guard
    guard = PFContextGuard(data_dir=data_dir, config=config)

    # Start all in threads
    def run_brain():
        brain.run()

    def run_scheduler():
        scheduler.run()

    def run_watchdog():
        watchdog.run()

    def run_guard():
        guard.run()

    def run_channel(ch):
        ch.run()

    t_brain = threading.Thread(target=run_brain, name="brain", daemon=True)
    t_sched = threading.Thread(target=run_scheduler, name="scheduler", daemon=True)
    t_watch = threading.Thread(target=run_watchdog, name="watchdog", daemon=True)
    t_guard = threading.Thread(target=run_guard, name="guard", daemon=True)
    threads.extend([t_brain, t_sched, t_watch, t_guard])

    for ch_name, ch in active_channels:
        if ch_name != "web":  # web doesn't need a polling thread
            t = threading.Thread(target=run_channel, args=(ch,), name=f"ch-{ch_name}", daemon=True)
            threads.append(t)

    log.info(f"starting {len(threads)} services...")
    for t in threads:
        t.start()
        log.info(f"  started: {t.name}")

    # Wait for shutdown signal
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("shutting down...")
        brain.running = False
        scheduler.running = False
        for _, ch in active_channels:
            ch.stop()


def main():
    parser = argparse.ArgumentParser(description="Permafrost Launcher")
    parser.add_argument("--config", "-c", help="Path to config.json")
    parser.add_argument("--status", "-s", action="store_true", help="Check service status")
    parser.add_argument("--stop", action="store_true", help="Stop all services")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.status:
        print("Permafrost Status:")
        check_status()
        return

    if args.stop:
        print("Stopping Permafrost:")
        stop_services()
        return

    config = load_config(args.config)
    if not config.get("ai_provider"):
        print("No config found. Run start.bat or create config.json first.")
        print("See config.example.json for reference.")
        sys.exit(1)

    print("=" * 50)
    print("  Permafrost — Starting all services")
    print("=" * 50)
    launch(config)


if __name__ == "__main__":
    main()
