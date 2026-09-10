# ADR-002 – PostgreSQL for Persistent State

**Status:** Proposed

## Context

Atlas requires durable storage for job, task, and worker metadata, as well as transactional guarantees for state transitions. Options considered:

1. **Relational Database (PostgreSQL)** – Strong ACID guarantees, mature tooling, support for JSONB, and easy integration with SQLAlchemy.
2. **NoSQL Document Store (e.g., MongoDB)** – Flexible schema but weaker transactional semantics.
3. **Key‑Value Store (e.g., Redis)** – Fast but lacks durability for long‑term state.

## Decision

We select **PostgreSQL** as the primary persistence layer.

### Rationale

- ACID transactions ensure reliable state transitions.
- Powerful querying capabilities for reporting and debugging.
- Widely available and easy to run locally via Docker.
- Compatibility with SQLAlchemy and Alembic for migrations.

### Trade‑offs

- Requires schema migrations for evolving data models.
- Slightly higher operational overhead compared to an in‑memory store.

## Consequences

- All domain entities (jobs, tasks, workers, task attempts) are stored in relational tables.
- Future phases will implement Alembic migrations for schema changes.
- Connection pooling and ORM usage will be standardized in the `persistence` package.
