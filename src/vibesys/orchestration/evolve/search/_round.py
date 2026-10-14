"""One round of children: the cold start, or one generation, and the order they are admitted in.

A round's children may finish in any order, but their outcomes are admitted only once every lower
slot has been, in slot order, so the population grows exactly as a serial run's would. That is
what makes a replay reach the same population whatever order the children finished in. A round
is a plain value of the root flow's record: it awaits nothing and reads nothing from the run.
"""

from dataclasses import dataclass, field

from vibesys.orchestration.evolve.search._values import ChildDone, ChildStart


@dataclass
class Round:
    """The children of a round in slot order, which have started and which have finished."""

    starts: tuple[ChildStart, ...]
    """The children that can run, lowest slot first. A slot with no parent to build on is not one."""
    width: int
    """Children that run at once."""
    generation_open: bool
    """Whether the round is a generation (the cold start is not)."""
    begun: int = 0
    finished: dict[str, ChildDone] = field(default_factory=dict)
    admitted: int = 0

    def ready(self, free: int, *, stopping: bool) -> list[ChildStart]:
        """The next children to start in ``free`` slots; none once the operator has stopped."""
        if stopping:
            return []
        taken = self.starts[self.begun : self.begun + max(free, 0)]
        self.begun += len(taken)
        return list(taken)

    def finish(self, done: ChildDone) -> list[ChildDone]:
        """Keep a finished child; the children to admit now, in slot order."""
        self.finished[done.start.key] = done
        ready: list[ChildDone] = []
        while self.admitted < self.begun:
            next_done = self.finished.get(self.starts[self.admitted].key)
            if next_done is None:
                break
            ready.append(next_done)
            self.admitted += 1
        return ready
