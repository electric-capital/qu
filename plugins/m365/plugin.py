"""Plugin entry point: thin re-export of the real manifest module.

The loader imports this file under a synthetic module name
(``quest_plugin_m365``); the real code lives in the normal
``plugins.m365.*`` package path so imports inside the plugin resolve to
a single module instance.
"""

from plugins.m365.manifest import get_plugin

__all__ = ["get_plugin"]
