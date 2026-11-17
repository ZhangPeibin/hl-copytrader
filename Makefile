SHELL := /bin/bash

.PHONY: dev api web test up logs down build

api:
	cd backend && DATA_DIR=../data uv run --frozen uvicorn hlcopy.main:create_app --factory --reload --host 127.0.0.1 --port 8080

web:
	cd frontend && npm run dev

dev:
	@echo "API http://127.0.0.1:8080  Web http://127.0.0.1:5173"
	@trap 'kill 0' EXIT; $(MAKE) api & $(MAKE) web & wait

test:
	cd backend && uv run --frozen pytest

up:
	docker compose up -d --build

logs:
	docker compose logs -f app

down:
	docker compose down

build:
	docker compose build
