"""
Example Permafrost Plugin — Demonstrates how to add custom tools.

This plugin registers a simple "hello" tool that the AI brain can use.
Copy this as a template to create your own plugins.

To create a plugin:
  1. Create a folder in plugins/ (e.g. plugins/my_plugin/)
  2. Add plugin.json with metadata
  3. Add __init__.py that registers your tools/channels
  4. Restart Permafrost — plugin auto-loads
"""

from core.tools import register_tool


@register_tool("hello", "Say hello to someone (example plugin tool)", {
    "name": {"type": "string", "description": "Name of the person to greet"},
})
def tool_hello(name="World", **kwargs):
    return f"Hello, {name}! This response came from the example plugin."


@register_tool("dice", "Roll a random dice (example plugin tool)", {
    "sides": {"type": "number", "description": "Number of sides (default: 6)"},
})
def tool_dice(sides=6, **kwargs):
    import random
    result = random.randint(1, int(sides))
    return f"Rolled a d{int(sides)}: {result}"
