"""Slack plugin entry point (thin re-export, per plugin convention)."""

from plugins.slack.manifest import get_plugin

__all__ = ["get_plugin"]
