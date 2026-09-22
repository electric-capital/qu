"""Plugin entry point: thin re-export of the real manifest module.

The loader imports this file under a synthetic module name
(``quest_plugin_iru``); the real code lives in the normal
``plugins.iru.*`` package path so imports inside the plugin resolve to a
single module instance.
"""

from plugins.iru.manifest import get_plugin

__all__ = ["get_plugin"]
