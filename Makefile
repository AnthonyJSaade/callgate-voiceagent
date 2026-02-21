.PHONY: up down logs test migrate seed psql

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

seed:
	docker compose -f infra/docker-compose.yml run --rm backend python -m scripts.seed_demo_business

psql:
	docker compose -f infra/docker-compose.yml exec postgres psql -U postgres -d voiceagent -c "select id, name, timezone from businesses;"
