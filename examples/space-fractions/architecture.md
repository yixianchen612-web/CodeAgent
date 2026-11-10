# Space Fractions — Architecture

## Overview

Space Fractions is a browser-based educational game that helps players practice
fractions. The original specification described a microservices architecture
with separate Game, Question, and User components. For this deliverable, the
architecture has been **simplified to a single deployable Node.js/Express
service** that serves a static HTML/JS frontend and a REST API backed by
PostgreSQL. The three original components are preserved as internal modules
within the single service.

## Architecture Decisions (Simplifications)

| Original design | Simplified design | Rationale |
| --- | --- | --- |
| Microservices (Game, Question, User) | Single Express service with internal modules | Reduces deployment and operational complexity while preserving component boundaries in code |
| Redis cache | In-memory caching within the service | Avoids an extra infrastructure dependency for a small-scale game |
| RabbitMQ messaging | Synchronous request handling | Game interactions are request/response; no async fan-out needed |
| Elasticsearch search | Direct PostgreSQL queries | Question volume is small; full-text search is unnecessary |
| OAuth2 authentication | Basic username/password registration + login with an `is_admin` flag | Simplifies auth while still separating user and admin roles |
| Jenkins CI/CD, Terraform | Docker + Kubernetes manifests only | Infrastructure provisioning is out of scope for this deliverable |

## Components

### GameComponent
- **Responsibilities**: Game flow, answer checking, score calculation, and
  user interaction. Owns the `games` table.
- **Technology options**:
  1. Node.js 18–20 + Express 4–5 (recommended) — meets NFR-1 (performance) and
     ASR-1 (data durability) via a mature, event-driven runtime.
  2. Python + FastAPI — strong typing and async, but less consistent with the
     frontend toolchain.
  3. Java + Spring Boot — robust but heavier for a small game.
- **Recommended stack**: Node.js 20 + Express 4, PostgreSQL 15.
- **API contract**: `openapi.yaml` (`/api/play`, `/api/score`).
- **Internal contract**: `internal.proto` (`GameService`).
- **Data schema**: `games` table in `sql/game_ddl.sql`.

### QuestionComponent
- **Responsibilities**: Question management and data persistence. Owns the
  `questions` table.
- **Technology options**:
  1. PostgreSQL 14–15 (recommended) — meets ASR-1 (data durability) with ACID
     transactions and replication support.
  2. MongoDB — flexible schema but weaker relational integrity.
  3. SQLite — zero-config but not suitable for multi-instance deployment.
- **Recommended stack**: PostgreSQL 15 with JSONB for options.
- **API contract**: `openapi.yaml` (`/api/questions`).
- **Data schema**: `questions` table in `sql/question_ddl.sql`.

### UserComponent
- **Responsibilities**: User authentication and authorization. Owns the
  `users` table.
- **Technology options**:
  1. OAuth2 (original recommendation) — meets ASR-2 (security) but adds
     complexity.
  2. Basic username/password with hashed passwords (recommended for this
     deliverable) — simple and sufficient for a demo.
  3. JWT-based sessions — good balance but requires token management.
- **Recommended stack**: bcrypt-style password hashing with an `is_admin`
  flag for role separation.
- **API contract**: `openapi.yaml` (`/api/users/register`, `/api/users/login`).
- **Data schema**: `users` table in `sql/game_ddl.sql`.

### AdminComponent
The component diagram references an `AdminComponent` that is not described in
the architecture text. It is implemented here as the `public/admin.html` UI and
the question management APIs (`/api/questions` CRUD), guarded by the `is_admin`
flag on users.

## Deployment Topology

The application is deployed as a containerized service on Kubernetes
(`k8s/spacefractions-deployment.yaml`) with a Deployment (2 replicas), a
ClusterIP Service, and a Secret for the database connection string. A
PostgreSQL instance is expected to be available at the `DATABASE_URL` provided
in the environment.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Scalability issues | Kubernetes Deployment with replicas; horizontal scaling via the stateless service |
| Security vulnerabilities | Password hashing, role separation, HTTPS termination at the ingress layer |
| Data loss | PostgreSQL persistence with regular backups and replication |

## QA Coverage

| Requirement | Test types |
| --- | --- |
| ASR-1 (data durability) | Unit testing, Integration testing |
| NFR-1 (performance) | Load testing, Stress testing |
| ASR-2 (security) | Penetration testing, Vulnerability scanning |

## Assumptions and Open Questions

- A single PostgreSQL database is shared by all modules.
- Authentication is simplified; production would use OAuth2 or JWT.
- The `sql/question_ddl.sql` file referenced by ASR-1 was not in the original
  deliverable list; it is included here to satisfy traceability.
- Load/stress and penetration testing are documented as QA coverage but are not
  automated in this repository.
