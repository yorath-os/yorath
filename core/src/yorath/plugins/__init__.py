"""Plugin/Extension API: Core extension point through entry points of the ``yorath.plugins`` group.

Enterprise modules and third-party extensions register as entry points and are loaded here;
Core works without a single plugin. The plugin contract will come in a separate change.
"""

from importlib.metadata import entry_points

ENTRY_POINT_GROUP = "yorath.plugins"


def discover() -> list[str]:
    """Names of registered plugins. The MVP has none; the function pins the mechanism."""
    return sorted(ep.name for ep in entry_points(group=ENTRY_POINT_GROUP))
