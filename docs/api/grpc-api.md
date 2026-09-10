# gRPC API

This document describes the gRPC service contracts for Atlas.

## Services

- **JobService** – Submit jobs, query status, cancel jobs.
- **WorkerService** – Register workers, send heartbeats, report task results.
- **SchedulerService** – (Future) introspection of scheduler state.

## Protobuf Definitions

The full protocol is defined in `proto/atlas.proto`. The gRPC API uses the same messages for consistency.

### Example RPCs

```proto
service JobService {
  rpc SubmitJob (SubmitJobRequest) returns (SubmitJobResponse);
  rpc GetJob (GetJobRequest) returns (GetJobResponse);
  rpc CancelJob (CancelJobRequest) returns (CancelJobResponse);
}

service WorkerService {
  rpc RegisterWorker (RegisterWorkerRequest) returns (RegisterWorkerResponse);
  rpc Heartbeat (HeartbeatRequest) returns (HeartbeatResponse);
  rpc ReportTaskResult (ReportTaskResultRequest) returns (ReportTaskResultResponse);
}
```

Implementation details will be added in later phases.
