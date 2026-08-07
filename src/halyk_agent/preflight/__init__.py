"""Dataset preflight / quarantine package.

Import concrete APIs from submodules at composition roots, for example:

- ``halyk_agent.preflight.models``
- ``halyk_agent.preflight.service``

This package ``__init__`` intentionally avoids eager imports so that importing
solver DTOs does not load discovery/quarantine implementation modules.
"""

from __future__ import annotations

__all__: list[str] = []
