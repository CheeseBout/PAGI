"""Design pattern strategies (Phase 14, SPEC §16).

Importing this package registers every available Strategy.
"""

from __future__ import annotations

from . import react  # noqa: F401  (side effect: registers "react")
from .base import RunContext, Strategy, StrategyUnavailable, get_strategy, register

# Phase 14b+ patterns register themselves on import.
try:  # pragma: no cover - present only from 14b
    from . import reflexion  # noqa: F401
except ImportError:
    pass
try:  # pragma: no cover
    from . import plan_execute  # noqa: F401
except ImportError:
    pass
try:  # pragma: no cover
    from . import router  # noqa: F401
except ImportError:
    pass
try:  # pragma: no cover
    from . import supervisor  # noqa: F401
except ImportError:
    pass
try:  # pragma: no cover
    from . import debate  # noqa: F401
except ImportError:
    pass
try:  # pragma: no cover
    from . import evaluator_optimizer  # noqa: F401
except ImportError:
    pass

__all__ = [
    "RunContext",
    "Strategy",
    "StrategyUnavailable",
    "get_strategy",
    "register",
]
