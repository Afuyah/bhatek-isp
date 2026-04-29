.PHONY: help install dev db-init db-migrate test lint clean docker-up docker-down

help:
	@echo "Available commands:"
	@echo "  make install     - Install dependencies"
	@echo "  make dev         - Run development server"
	@echo "  make db-init     - Initialize database"
	@echo "  make db-migrate  - Run database migrations"
	@echo "  make test        - Run tests"
	@echo "  make lint        - Run linter"
	@echo "  make docker-up   - Start Docker containers"
	@echo "  make docker-down - Stop Docker containers"

install:
	pip install -r requirements.txt

dev:
	FLASK_ENV=development flask run --host=0.0.0.0 --port=5000 --reload

db-init:
	flask db init
	flask db migrate -m "Initial migration"
	flask db upgrade

db-migrate:
	flask db migrate
	flask db upgrade

test:
	pytest tests/ -v --cov=app --cov-report=html

lint:
	flake8 app/ --max-line-length=120 --exclude=migrations
	black app/ --check
	isort app/ --check-only

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

celery-worker:
	celery -A app.tasks.celery_app:celery worker --loglevel=info --concurrency=4

celery-beat:
	celery -A app.tasks.celery_app:celery beat --loglevel=info