DEFAULT_SCHEDULE = [
    {
        "id": "heartbeat",
        "name": "System Heartbeat",
        "interval_seconds": 300,
        "enabled": True,
        "task": "Check system health and write heartbeat file",
    },
    {
        "id": "self-check",
        "name": "Self Check",
        "interval_seconds": 3600,
        "enabled": True,
        "task": "Review recent conversations for mistakes, log pitfalls if found",
    },
    {
        "id": "evolution",
        "name": "Self Evolution",
        "interval_seconds": 7200,
        "enabled": True,
        "task": "Check evolution queue, pick next item, execute improvement",
    },
]
