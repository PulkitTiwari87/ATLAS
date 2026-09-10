# Phase 00 — Foundation

## Objective

Establish the minimal development foundation required to begin implementing Atlas safely.

This phase must NOT implement distributed scheduling, workers, retries, heartbeats, failure detection, or other later-phase functionality.

## Scope

### Python Project

Establish the Python package structure and project configuration.

Requirements:

- Valid `pyproject.toml`
- Importable Atlas package
- Clear development entry point
- Python version constraint
- Dependency configuration

### Configuration

Create the configuration foundation required by Atlas.

Configuration should support environment-based values for:

- Database connection
- Atlas host
- Atlas ports
- Logging level
- Worker timing values
- Retry configuration

Do not hardcode environment-specific production values.

### Logging

Establish basic application logging.

Requirements:

- Consistent logging setup
- Configurable log level
- Useful timestamps/context
- No unnecessary logging framework complexity

### Testing

Establish the pytest testing foundation.

Requirements:

- Test discovery works
- At least one meaningful foundation test
- Tests can run locally

### PostgreSQL Development Environment

Establish the local PostgreSQL development environment required by later phases.

Docker Compose may be used.

Do not implement the database schema yet.

Do not implement repositories yet.

### Docker

Provide the minimal Docker development configuration required by the project.

Avoid unnecessary services.

Do not introduce Redis, Kafka, RabbitMQ, Celery, Kubernetes, or other infrastructure.

### Project Conventions

Ensure the repository follows the engineering rules in `CLAUDE.md`.

## Out of Scope

Do NOT implement:

- Job lifecycle
- Task lifecycle
- Worker runtime
- Scheduler
- Priority queue
- Dispatch
- Heartbeats
- Failure detection
- Retries
- Recovery
- REST job endpoints
- gRPC implementation
- Production authentication
- Metrics infrastructure
- Distributed locking
- Advanced scheduling policies

Those belong to later phases.

## Expected Result

At the end of Phase 00:

1. Atlas has a valid Python project structure.
2. Configuration can be loaded.
3. Logging works.
4. pytest works.
5. PostgreSQL can be started locally.
6. Docker development infrastructure is available.
7. Existing documentation remains accurate.
8. No later-phase functionality has been implemented.

## Acceptance Criteria

### Project

- [ ] Python project installs successfully.
- [ ] Atlas package imports successfully.
- [ ] Project entry point is valid.

### Configuration

- [ ] Configuration loads successfully.
- [ ] Environment variables can override defaults.
- [ ] No secrets are committed.

### Logging

- [ ] Application logging initializes successfully.
- [ ] Log level is configurable.

### Testing

- [ ] pytest discovers tests.
- [ ] Foundation tests pass.

### PostgreSQL

- [ ] PostgreSQL service starts successfully.
- [ ] Atlas can reach the configured database.

### Docker

- [ ] Docker Compose configuration is valid.
- [ ] Required development services start successfully.

### Scope

- [ ] No scheduler implementation.
- [ ] No worker execution implementation.
- [ ] No retry/recovery implementation.
- [ ] No heartbeat/failure detection implementation.

## Verification

Run the project's configured test command.

Verify:

- package installation
- imports
- configuration
- logging
- database connectivity
- Docker configuration

## Completion

Phase 00 is complete only when all acceptance criteria pass.

After completion, produce the concise phase report specified in `CLAUDE.md`.