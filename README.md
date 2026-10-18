# FestFlow — Spectra 2026 Ticketing & Event Ops

College-fest ticketing, check-in, volunteer scheduling and live analytics, built on
**MongoDB 8.0 + raw PyMongo + Django 5.2** (Django for web only; no ORM, no SQL database).

Course project for CSE494 — Intelligent NoSQL Databases.

> Status: **Phase 0 (scaffold)**. Later phases add validators, seed data, CRUD, aggregations, UI, tests and docs.

## Prerequisites

- Python 3.12+ (developed on 3.13)
- Docker Desktop (for MongoDB 8.0) — or a local MongoDB 8.0 on port 27017
- Optional: mongosh, MongoDB Compass

## Setup

### Windows PowerShell

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env        # then set DJANGO_SECRET_KEY
docker compose up -d
python manage.py runserver
```

### bash

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # then set DJANGO_SECRET_KEY
docker compose up -d
python manage.py runserver
```

Open http://127.0.0.1:8000/health/ — it should show *Connected (ping ok)*, the server
version, and *make_password / check_password work*.

> If a native MongoDB service already listens on 27017, `docker compose up` cannot bind
> that port. Either stop the native service or use it instead of Docker.

## Tests

```bash
python -m pytest
```

## Project layout

Layering rule: **views → services → repositories → PyMongo**. Only `repositories/`
(and the client factory in `core/db.py`) import PyMongo.

```
festflow/       Django project (settings, urls, wsgi)
core/           Mongo client, schemas, indexes, auth, converters, management commands
repositories/   all PyMongo calls
services/       business rules (no PyMongo)
web/            views, forms, templates
queries/        mongosh twins of every PyMongo query
tests/          pytest + pytest-django
docs/           report, demo script, viva questions, screenshots
```
