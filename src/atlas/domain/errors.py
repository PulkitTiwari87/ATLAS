"""Domain-level errors for Atlas."""


class InvalidTransitionError(Exception):
    """Raised when an entity is moved to a status it cannot legally reach."""

    def __init__(self, entity: str, current, target):
        super().__init__(
            f"{entity} cannot transition from {current.value} to {target.value}"
        )
        self.entity = entity
        self.current = current
        self.target = target
