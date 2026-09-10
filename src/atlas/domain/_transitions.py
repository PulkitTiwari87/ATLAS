"""Shared state-transition validation for domain entities."""

from atlas.domain.errors import InvalidTransitionError


def ensure_transition_allowed(entity: str, current, target, allowed: dict) -> None:
    if target not in allowed.get(current, set()):
        raise InvalidTransitionError(entity, current, target)
