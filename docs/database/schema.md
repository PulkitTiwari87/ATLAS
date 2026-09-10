# Database Schema

The core tables for Atlas are:

## jobs
- `id` UUID PK
- `name` TEXT
- `status` VARCHAR (job lifecycle enum)
- `priority` INTEGER
- `created_at` TIMESTAMP
- `updated_at` TIMESTAMP

## tasks
- `id` UUID PK
- `job_id` UUID FK → jobs.id
- `type` VARCHAR (e.g., `shell`, `python`)
- `payload` JSONB (task‑specific parameters)
- `status` VARCHAR (task lifecycle enum)
- `attempt` INTEGER (current retry count)
- `max_retries` INTEGER
- `worker_id` UUID FK → workers.id (nullable)
- `created_at` TIMESTAMP
- `updated_at` TIMESTAMP

## workers
- `id` UUID PK
- `hostname` TEXT
- `address` TEXT
- `status` VARCHAR (worker lifecycle enum)
- `last_heartbeat` TIMESTAMP
- `created_at` TIMESTAMP

## task_attempts
- `id` UUID PK
- `task_id` UUID FK → tasks.id
- `worker_id` UUID FK → workers.id
- `attempt_number` INTEGER
- `started_at` TIMESTAMP
- `finished_at` TIMESTAMP
- `result` JSONB (optional output metadata)
- `error` TEXT (optional error message)

### Indexes
- `jobs.status`
- `tasks.status`
- `tasks.worker_id`
- `workers.status`
- `task_attempts.task_id`

All tables include `ON DELETE CASCADE` foreign keys where appropriate.
