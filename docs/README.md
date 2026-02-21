## Repository Structure

- `backend/`: FastAPI API service, tests, and webhook-ready app scaffolding.
- `infra/`: local development infrastructure (`docker-compose.yml`, env templates).
- `docs/`: project context, API contracts, prompts, tool specs, and runbooks.

## Local Development

1. Start services:
   - `make up`
2. Health check:
   - `GET http://localhost:8000/health`
   - Expected response: `{"ok": true}`
3. Run tests:
   - `make test`
4. Stop services:
   - `make down`

## Commands

- `make up`: `docker compose -f infra/docker-compose.yml up --build`
- `make down`: `docker compose -f infra/docker-compose.yml down`
- `make logs`: stream `backend` and `postgres` logs
- `make test`: run backend pytest suite in container
