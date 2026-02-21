.PHONY: up down logs test migrate migrate-cloudsql seed psql

up:
	docker compose -f infra/docker-compose.yml up --build

down:
	docker compose -f infra/docker-compose.yml down

logs:
	docker compose -f infra/docker-compose.yml logs -f backend postgres

test:
	docker compose -f infra/docker-compose.yml run --rm backend pytest -q

migrate:
	docker compose -f infra/docker-compose.yml run --rm backend alembic upgrade head

migrate-cloudsql:
	@test -n "$(DATABASE_URL)" || (echo "ERROR: DATABASE_URL is required. Usage: DATABASE_URL=... make migrate-cloudsql" && exit 1)
	cd backend && DATABASE_URL="$(DATABASE_URL)" python -m alembic upgrade head

seed:
	docker compose -f infra/docker-compose.yml run --rm backend python -m scripts.seed_demo_business

psql:
	docker compose -f infra/docker-compose.yml exec postgres psql -U postgres -d voiceagent -c "select id, name, timezone from businesses;"
