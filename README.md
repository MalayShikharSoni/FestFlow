# FestFlow — Spectra 2026 Ticketing & Event Ops

College-fest ticketing, check-in, volunteer scheduling and live analytics, built on
**MongoDB 8.0 + raw PyMongo + Django 5.2** (Django for web only; no ORM, no SQL database).

Course project for CSE494 — Intelligent NoSQL Databases.

> Status: **Phase 5 (web UI)**. Later phases add tests and docs.

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

### Initialise the database (validators + indexes)

```bash
python manage.py init_db                      # idempotent; re-run after changing core/schemas.py
```

The same setup in mongosh, plus a demo of writes the validators reject:

```bash
docker exec -it festflow-mongo mongosh spectra /queries/00_setup.js
docker exec -it festflow-mongo mongosh spectra /queries/01_validation_demo.js
# without Docker (native mongod + mongosh on PATH):
mongosh spectra queries/00_setup.js
mongosh spectra queries/01_validation_demo.js
```

### Seed demo data

```bash
python manage.py seed --reset                  # 5,000 bookings; drops the DB, runs init_db, then verify_data
python manage.py seed --reset --bookings 50000 # bigger dataset for the explain() comparisons
python manage.py verify_data                   # re-check invariants any time
mongosh spectra queries/05_verify_data.js      # the same checks in mongosh
```

CRUD twins of every repository query, and an idempotent venue import (upsert by name):

```bash
mongosh spectra queries/02_crud.js             # runs every repository query; demo writes are cleaned up
python manage.py import_venues data/sample_venues.json   # run twice: second run creates nothing
```

### Using the web app

`python manage.py runserver`, then open http://127.0.0.1:8000/

| Who | Pages |
|---|---|
| Anyone | Event list (type/day filters, `$text` search, pagination), event detail, sign up, log in |
| Attendee | Book tickets, My bookings, ticket page with QR codes, cancel |
| Volunteer | My shifts |
| Organizer | Dashboard (`$facet` KPIs + charts), events (create/edit with tier rows, cancel, delete), venues, gate check-in, volunteer shifts + coverage, **Query Lab** |

### Aggregations and indexes

```bash
mongosh spectra queries/03_aggregations.js     # all 11 analytics pipelines (+ volunteer coverage)
python manage.py seed --reset --bookings 50000 # big dataset makes index effects obvious
python manage.py explain_report                # writes docs/explain_results.md (drops/recreates indexes: dev only)
mongosh spectra queries/04_indexes_explain.js  # the same before/after explain() in mongosh
```

The fest is placed relative to today: days 1-3 are the three previous days
(completed, with check-ins), today is a break, days 4-5 are the next two days
(scheduled, bookable). Re-seed on another day and the fest moves with you.

Demo logins (password `Spectra@2026`): `organizer@spectra.test`,
`attendee@spectra.test`, `volunteer@spectra.test`.

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
