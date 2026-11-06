.PHONY: help install dev run migrate revision test lint clean docker-up docker-down docker-logs celery-worker celery-beat

PYTHON ?= python
FLASK_APP := wsgi.py
export FLASK_APP

help:
	@echo "bhatek-isp"
	@echo "  make install        install dependencies into the active venv"
	@echo "  make dev            run the dev server on :5000 with reload"
	@echo "  make run            run gunicorn with the production config"
	@echo "  make migrate        apply database migrations"
	@echo "  make revision m=..  autogenerate a migration"
	@echo "  make test           run the test suite"
	@echo "  make lint           style checks"
	@echo "  make docker-up      start the full stack (web, worker, beat, db, redis)"
	@echo "  make docker-down    stop the stack"

install:
	$(PYTHON) -m pip install -r requirements.txt

dev:
	FLASK_ENV=development flask run --host=0.0.0.0 --port=5000 --reload

run:
	gunicorn --config gunicorn.conf.py wsgi:app

migrate:
	flask db upgrade

revision:
	flask db migrate -m "$(m)"

test:
	$(PYTHON) -m pytest tests/ -v

lint:
	@echo "no linter is installed by requirements.txt yet; skipping"

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache htmlcov .coverage

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f web

celery-worker:
	celery -A app.celery_app worker --loglevel=info --concurrency=4

celery-beat:
	celery -A app.celery_app beat --loglevel=info
