"""Capture records from the non-propagating ``project_atlas`` logger tree.

``configure_logging`` (``src/project_atlas/logging.py``) attaches the package's own
stderr handler to the ``project_atlas`` logger and sets ``propagate = False`` on it, so
vault content printed to stdout is never interleaved with logs. ``get_logger`` calls
``configure_logging()`` on first use, which every module does at import, so the tree is
severed from root as soon as ``project_atlas`` is imported at all.

``caplog`` installs its handler on the **root** logger. Records from
``project_atlas.<module>`` therefore never reach it -- unless the installed pytest also
attaches that handler further down. pytest 9.1 attaches it to every logger that is
already non-propagating when the phase starts; 8.3 and 9.0 do not, and ``pyproject.toml``
permits all three via ``pytest>=8.0``.

Assertions written against ``caplog`` for these loggers are therefore observable or
invisible purely according to which pytest happens to be installed. Positive assertions
(``assert any(...)``) fail loudly on the older versions. **Negative** assertions
(``assert not [m for m in caplog.messages if ...]``) are worse: they pass vacuously,
reporting confidence in a "this must never warn" guarantee that was never checked.

``capturing()`` removes the version dependence by attaching the handler explicitly. It
weakens nothing -- every assertion still runs unchanged, against the same records the
package emits to stderr either way.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

DISCOVERY_LOGGER = "project_atlas.discovery"


def _already_captured(logger: logging.Logger, handler: logging.Handler) -> bool:
    """Is ``handler`` already reachable from ``logger``'s own propagation chain?

    Walks up while propagation holds, stopping at the first logger that does not
    propagate -- which is exactly how far a record emitted here would travel. On
    pytest 9.1 the answer is True (it attached the same handler to ``project_atlas``),
    so attaching again would deliver every record twice and quietly break any
    count-based assertion. On 8.3/9.0 it is False and the attachment is what makes
    the records visible at all.
    """
    current: logging.Logger | None = logger
    while current is not None:
        if handler in current.handlers:
            return True
        if not current.propagate:
            return False
        current = current.parent
    return False


@contextmanager
def capturing(
    caplog: pytest.LogCaptureFixture,
    logger_name: str = DISCOVERY_LOGGER,
    level: int = logging.WARNING,
) -> Iterator[None]:
    """Make ``caplog`` see ``logger_name``'s records on every pytest the project declares."""
    logger = logging.getLogger(logger_name)
    attached = not _already_captured(logger, caplog.handler)
    if attached:
        logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(level, logger=logger_name):
            yield
    finally:
        if attached:
            logger.removeHandler(caplog.handler)
