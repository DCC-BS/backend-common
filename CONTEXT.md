# Context — dcc-backend-common

Shared Python code (config, logging, FastAPI error handling, health probes, LLM agent helpers) consumed by the backend services of the Data Competence Center Basel-Stadt.

## Glossary

This is a ubiquitous-language glossary for the library. It is intentionally free of implementation detail.

- **Health Probe**
  An HTTP endpoint exposed by a service so an orchestrator (Kubernetes) can query the liveness, readiness, or startup state of a pod.

- **Liveness Probe**
  A health probe that answers "is the process alive and not deadlocked?". On failure Kubernetes restarts the container. It must NOT check external dependencies.

- **Readiness Probe**
  A health probe that answers "is the pod ready to serve traffic?". It checks critical **Service Dependencies**. On failure Kubernetes stops routing traffic to the pod.

- **Startup Probe**
  A health probe that gates liveness/readiness until initialisation (e.g. loading a model) is complete.

- **Service Dependency**
  A downstream service the application calls and depends on to serve requests. Each has a name, a `health_check_url`, and an optional API key.

- **Error Signature**
  The stable key used to tell one failure apart from another for a given Service Dependency. Form: `http:{status}` for a non-200 HTTP response, or `{ExceptionClassName}` for a transport/connection error. The raw, volatile message text is deliberately excluded.

- **First Occurrence**
  The first Readiness Probe that observes a given (Service Dependency, Error Signature) after a period of health (or after a signature change). It is the one probe that is logged at full detail.

- **Suppressed Probe**
  A Readiness Probe whose failure log is intentionally dropped because its (Service Dependency, Error Signature) was already logged on its First Occurrence and has not changed.

- **Heartbeat**
  A periodic summary log, emitted while a Service Dependency stays unhealthy, so that a long-running **Outage** never disappears from the logs entirely.

- **Recovery Summary**
  A summary log emitted at the moment a Service Dependency transitions unhealthy → healthy, reporting the just-ended Outage.

- **Outage**
  The interval for a single (Service Dependency, Error Signature) from First Occurrence until either recovery or a signature change.
