"""Position-ordered work with obsolete, dominated configurations discarded."""

from __future__ import annotations

import heapq
from collections.abc import Iterator
from itertools import count

from .execution import Configuration
from .frontier import ConfigurationFrontier


class PendingConfigurations:
    def __init__(self) -> None:
        self._queue: list[tuple[int, int, Configuration]] = []
        self._serial = count()
        self._frontier = ConfigurationFrontier()

    def offer(self, current: Configuration) -> None:
        if self._frontier.add(current):
            heapq.heappush(
                self._queue, (current.state.position, next(self._serial), current)
            )

    def remaining(self) -> bool:
        return bool(self._queue)

    def active(self, current: Configuration) -> bool:
        return self._frontier.active(current)

    def at_next_position(self) -> Iterator[Configuration]:
        position = self._queue[0][0]
        while self._queue and self._queue[0][0] == position:
            _, _, current = heapq.heappop(self._queue)
            if self.active(current):
                yield current
