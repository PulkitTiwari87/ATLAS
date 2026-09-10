# Component Architecture

This document outlines the responsibilities of each core component within Atlas.

## API Layer
* Exposes REST and gRPC endpoints for external clients and workers.
* Performs request validation and authentication (to be added later).

## Job Manager
* Manages job lifecycle state transitions.
* Persists job metadata and aggregates task results.

## Scheduler
* Maintains a priority queue of pending tasks.
* Selects the next task based on priority and worker availability.

## Dispatcher
* Assigns selected tasks to available workers.
* Tracks assignment acknowledgments and retries on failure.

## Worker Registry
* Records worker registration, capabilities, and health status.
* Updates worker state based on heartbeats.

## Worker Runtime
* Executes assigned tasks, handles environment setup, and reports results.

## Heartbeat Manager
* Receives periodic heartbeat messages from workers.
* Updates `last_seen` timestamps and detects stale workers.

## Failure Detector
* Monitors heartbeat timeouts.
* Marks workers as unhealthy and triggers task recovery.

## Recovery Manager
* Identifies tasks left in an inconsistent state due to worker failure.
* Requeues tasks for rescheduling and respects retry limits.

## PostgreSQL (Persistence Layer)
* Stores durable state for jobs, tasks, workers, and heartbeat information.
* Provides transactional guarantees for state transitions.

Each component will be implemented in its own module under `src/atlas/`.
