"""gRPC control-plane transport (Phase 10) — replaces Phase 06's in-process
WorkerRegistry for workers running as separate processes. Handlers are thin
adapters over the existing Phase 02/04/07 repositories/domain logic; no new
business logic lives here.
"""
