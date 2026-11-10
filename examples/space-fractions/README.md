# Space Fractions

A browser-based educational game that helps players practice fractions. The
application serves a static HTML/JS frontend (intro, gameplay, score, help, and
admin screens) backed by a REST API and a PostgreSQL database.

## Features

- **Play Game** — answer fraction questions and earn points.
- **View Score** — see recent game scores.
- **Help** — instructions and tips.
- **Admin** — create, read, update, and delete questions.
- **Users** — register and log in (basic authentication).

## Tech Stack

- Node.js 18–20
- Express 4
- PostgreSQL 14–15
- Plain HTML/CSS/JavaScript frontend

## Prerequisites

- Node.js 18 or later
- PostgreSQL 14 or later (running locally or via a connection string)

## Install Dependencies

```bash
npm install
```

## Database Setup

Create the database and apply the schema. The schema is split into two DDL
files: `sql/game_ddl.sql` (users and games) and `sql/question_ddl.sql`
(questions).

```bash
# Create the database (if it does not exist)
createdb -U postgres spacefractions

# Apply the schema
psql -U postgres -d spacefractions -f sql/game_ddl.sql
psql -U postgres -d spacefractions -f sql/question_ddl.sql
```

The application also attempts to apply the schema automatically on startup
(see `src/db.js`), so the manual step above is optional if the database already
exists and the connection string is correct.

### Connection String

Set the `DATABASE_URL` environment variable (or create a `.env` file):

```bash
export DATABASE_URL="postgres://postgres:postgres@localhost:5432/spacefractions"
```

The default connection string is
`postgres://postgres:postgres@localhost:5432/spacefractions`.

## Run

```bash
npm start
```

Then open http://localhost:3000 in your browser.

## Test

Tests use the Node.js built-in test runner and do not require a live database
or any external services. Unit tests stub the model layer, and integration
tests exercise the controllers directly with mock request/response objects.

```bash
npm test
```

## API

The REST API is documented in `openapi.yaml`. Key endpoints:

- `POST /api/play` — start a game
- `POST /api/play/:gameId/answer` — submit an answer
- `GET /api/score/:gameId` — get a game's score
- `GET /api/scores` — list recent scores
- `GET/POST /api/questions` — list/create questions
- `PUT/DELETE /api/questions/:id` — update/delete a question
- `POST /api/users/register` — register a user
- `POST /api/users/login` — log in a user
- `GET /api/health` — health check

The internal gRPC contract is defined in `internal.proto`.

## Deployment

A Kubernetes manifest is provided at `k8s/spacefractions-deployment.yaml`
(Deployment with 2 replicas, ClusterIP Service, and a database Secret). A
`Dockerfile` is included for building the container image.

```bash
docker build -t spacefractions:latest .
```

## Architecture Decisions

The original specification described a microservices architecture. This
deliverable simplifies it to a single deployable service. See
`architecture.md` for the full rationale. Key simplifications:

1. **Microservices → single service**: Game, Question, and User components are
   implemented as internal modules (`src/services`, `src/models`) within one
   Express app to reduce deployment complexity.
2. **Redis/RabbitMQ/Elasticsearch omitted**: in-memory caching and synchronous
   request handling are used; question volume is small enough for direct
   PostgreSQL queries.
3. **OAuth2 → basic auth**: registration/login with hashed passwords and an
   `is_admin` flag for role separation.
4. **`sql/question_ddl.sql` added**: the spec's ASR-1 referenced this file but
   it was missing from the deliverable list; both `game_ddl.sql` and
   `question_ddl.sql` are included to satisfy traceability.
5. **AdminComponent implemented**: the component diagram referenced an
   `AdminComponent` not described in the architecture text; it is realized as
   the `public/admin.html` UI and question CRUD APIs.

## Project Structure

```
├── architecture.md
├── openapi.yaml
├── internal.proto
├── traceability_matrix.csv
├── Dockerfile
├── k8s/spacefractions-deployment.yaml
├── sql/
│   ├── game_ddl.sql
│   └── question_ddl.sql
├── src/
│   ├── server.js
│   ├── app.js
│   ├── db.js
│   ├── routes/
│   ├── controllers/
│   ├── services/
│   └── models/
├── public/
│   ├── index.html
│   ├── game.html
│   ├── score.html
│   ├── help.html
│   ├── admin.html
│   ├── css/style.css
│   └── js/
└── tests/
    ├── unit/
    └── integration/
```
