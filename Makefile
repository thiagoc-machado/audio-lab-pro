up:
	docker compose up --build -d
down:
	docker compose down
logs:
	docker compose logs -f
test:
	docker compose exec web pytest
lint:
	docker compose exec web ruff check app tests
migrate:
	docker compose exec web alembic upgrade head
smoke:
	docker compose exec web python scripts/smoke.py
dev:
	docker compose -f compose.yaml -f compose.dev.yaml up --build
