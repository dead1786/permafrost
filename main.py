"""
Permafrost — Main entry point.

Starts all services in the correct order:
  1. Brain (persistent AI session)
  2. Scheduler (cron-like task engine)
  3. Channel daemons (Telegram, Discord polling)
  4. Watchdog (monitors everything)
  5. Console (Web UI, optional)

Usage:
  python main.py                    # start all
  python main.py --no-console       # headless (no web UI)
  python main.py --config path.json # custom config
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from core.brain import PFBrain
from core.scheduler import PFScheduler
from core.watchdog import PFWatchdog
from core.guard import PFContextGuard
from core.notifier import PFNotifier
from smart.night_silence import PFNightSilence

# Import channels to trigger registration
import channels  # noqa: F401
from channels.base import create_channel

log = logging.getLogger("permafrost")


def load_config(path: str = None) -> dict:
    """Load config from file or default location."""
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    default = Path.home() / ".permafrost" / "config.json"
    if default.exists():
        with open(default, "r", encoding="utf-8") as f:
            return json.load(f)

    return {}


def start_brain(config: dict, channel_instances: dict) -> PFBrain:
    """Initialize and configure the brain with all channels."""
    brain = PFBrain(config_path=None)
    # Override config from unified config
    brain.config.update({
        "ai_provider": config.get("ai_provider", "claude"),
        "api_key": config.get("api_key", ""),
        "ai_model": config.get("ai_model", ""),
        "system_prompt": config.get("system_prompt", ""),
        "data_dir": config.get("data_dir", ""),
    })

    # Register channels with brain
    for name, ch in channel_instances.items():
        brain.register_channel(name, str(ch.inbox_file), ch.reply_handler)

    return brain


def start_channels(config: dict) -> dict:
    """Create and start enabled channel instances."""
    instances = {}

    # Always start web channel
    from channels.web import PFWeb
    web = PFWeb(config=config, data_dir=config.get("data_dir"))
    instances["web"] = web

    # Telegram
    if config.get("telegram_enabled"):
        from channels.telegram import PFTelegram
        tg = PFTelegram(config=config, data_dir=config.get("data_dir"))
        ok, err = tg.validate()
        if ok:
            instances["telegram"] = tg
        else:
            log.warning(f"telegram skipped: {err}")

    # Discord
    if config.get("discord_enabled"):
        from channels.discord import PFDiscord
        dc = PFDiscord(config=config, data_dir=config.get("data_dir"))
        ok, err = dc.validate()
        if ok:
            instances["discord"] = dc
        else:
            log.warning(f"discord skipped: {err}")

    return instances


def run_channel_thread(channel):
    """Run a channel's polling loop in a daemon thread."""
    try:
        channel.run()
    except Exception as e:
        log.error(f"channel {channel.name} crashed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Permafrost AI Brain Framework")
    parser.add_argument("--config", help="Path to config.json")
    parser.add_argument("--no-console", action="store_true", help="Don't start web console")
    parser.add_argument("--console-port", type=int, default=8503, help="Web console port")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    # Logging
    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("=" * 50)
    log.info("  Permafrost — AI Brain Framework")
    log.info("=" * 50)

    # Load config
    config = load_config(args.config)
    if not config.get("ai_provider"):
        log.warning("No config found. Run start.bat or visit http://localhost:8503 to set up.")
        if not args.no_console:
            _launch_console(args.console_port)
        return

    data_dir = config.get("data_dir", str(Path.home() / ".permafrost"))
    config["data_dir"] = data_dir
    Path(data_dir).mkdir(parents=True, exist_ok=True)

    log.info(f"Provider: {config.get('ai_provider')} / Model: {config.get('ai_model', 'default')}")
    log.info(f"Data dir: {data_dir}")

    # ── Start channels ──
    channel_instances = start_channels(config)
    log.info(f"Channels: {list(channel_instances.keys())}")

    # Start channel polling threads (except web which doesn't poll)
    channel_threads = []
    for name, ch in channel_instances.items():
        if name != "web":  # web doesn't need polling
            t = threading.Thread(target=run_channel_thread, args=(ch,),
                                 name=f"channel-{name}", daemon=True)
            t.start()
            channel_threads.append(t)
            log.info(f"  started {name} channel thread")

    # ── Start brain ──
    brain = start_brain(config, channel_instances)

    # ── Start scheduler ──
    scheduler = PFScheduler(data_dir=data_dir)
    sched_thread = threading.Thread(target=scheduler.run, name="scheduler", daemon=True)
    sched_thread.start()
    log.info("  started scheduler thread")

    # ── Start context guard ──
    guard = PFContextGuard(data_dir=data_dir)
    guard_thread = threading.Thread(target=guard.run, name="guard", daemon=True)
    guard_thread.start()
    log.info("  started context guard thread")

    # ── Start watchdog ──
    watchdog = PFWatchdog(data_dir=data_dir)
    watchdog.register_service("brain",
                              str(Path(data_dir) / "brain-heartbeat.json"),
                              [sys.executable, str(Path(__file__).parent / "main.py")])
    watchdog.register_service("scheduler",
                              str(Path(data_dir) / "scheduler-heartbeat.json"),
                              [sys.executable, "-m", "core.scheduler"])
    wd_thread = threading.Thread(target=watchdog.run, name="watchdog", daemon=True)
    wd_thread.start()
    log.info("  started watchdog thread")

    # ── Start console ──
    console_proc = None
    if not args.no_console:
        console_proc = _launch_console(args.console_port)

    # ── Shutdown handler ──
    def shutdown(sig, frame):
        log.info("shutdown signal received...")
        brain.running = False
        scheduler.running = False
        for ch in channel_instances.values():
            ch.stop()
        if console_proc:
            console_proc.terminate()
        log.info("goodbye.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log.info("")
    log.info("All services started. Press Ctrl+C to stop.")
    if not args.no_console:
        log.info(f"  Web Console: http://localhost:{args.console_port}")
    log.info("")

    # ── Run brain (blocking) ──
    brain.run()


def _launch_console(port: int):
    """Launch Streamlit console as subprocess."""
    console_dir = Path(__file__).parent / "console"
    app_path = console_dir / "app.py"
    if not app_path.exists():
        log.warning("console app.py not found, skipping")
        return None

    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app_path),
        "--server.port", str(port),
        "--server.headless", "true",
        "--server.address", "0.0.0.0",
    ]
    log.info(f"  starting console on port {port}")
    try:
        if sys.platform == "win32":
            proc = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            proc = subprocess.Popen(cmd, start_new_session=True)
        return proc
    except Exception as e:
        log.warning(f"console launch failed: {e}")
        return None


if __name__ == "__main__":
    main()
