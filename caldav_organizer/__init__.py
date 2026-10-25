"""CalDAV Organizer plugin for Hermes — calendar + tasks over CalDAV.

Registers the ``calendar`` toolset (events and to-dos). Credentials are read
per-profile from the environment, so each routed Hermes profile talks to its
own CalDAV account plus a shared collection. See ``tools.py``.
"""

from .tools import register

__all__ = ["register"]
