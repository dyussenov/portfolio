PYTHON := .venv/bin/python
MANAGE := $(PYTHON) manage.py
HOST ?= 127.0.0.1
PORT ?= 8000

.DEFAULT_GOAL := run
.PHONY: run setup migrate test superuser import-students sync sync-apply

run: setup migrate ## Запустить локальный сервер (make run PORT=8080)
	$(MANAGE) runserver $(HOST):$(PORT)

setup: .venv/.installed .env ## Создать .venv, поставить зависимости и создать .env

.venv/.installed: requirements.txt
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	touch $@

# Существующий .env не перезаписывается
.env:
	cp .env.local.example .env

migrate: setup ## Применить миграции
	$(MANAGE) migrate

test: setup ## Запустить тесты
	$(MANAGE) test lessons

superuser: setup ## Создать администратора
	$(MANAGE) createsuperuser

import-students: setup ## Импортировать учеников из CSV
	$(MANAGE) import_students

sync: setup ## Пробная синхронизация с AlfaCRM (ничего не меняет)
	$(MANAGE) sync_alfacrm

sync-apply: setup ## Загрузить учеников из AlfaCRM в локальную базу (в CRM ничего не пишет)
	$(MANAGE) sync_alfacrm --apply
