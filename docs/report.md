# FestFlow — Spectra 2026 Ticketing & Event Ops on MongoDB

**Course:** CSE494 — Intelligent NoSQL Databases, Lovely Professional University
**Stack:** MongoDB 8.0 · PyMongo 4 (synchronous) · Django 5.2 (web layer only) · Python 3.13
**Repository:** https://github.com/MalayShikharSoni/FestFlow

> Spectra 2026 is a fictional fest. All people, payments and artists in the dataset are generated.

---

## 1. Problem and real-world relevance

A college fest such as **Spectra 2026** runs about 25 events over five days across six campus venues:
ticketed pro shows for thousands of people, cultural competitions (some free), and small hands-on
workshops. The organizers need to:

- **sell tickets without overselling** — when 50 people press "Buy" for the last 10 seats, exactly 10 must succeed;
- register people for workshops with a hard seat limit (one seat per person);
- **check attendees in at the gate**, where the same QR code may be scanned twice (or at two gates at once);
- schedule volunteers into shifts that fit inside events and never overlap;
- see **live analytics**: revenue, occupancy, no-shows, check-in traffic, booking lead times.

Today this is typically done with Google Forms and spreadsheets. Forms have no notion of stock, so they
oversell; spreadsheets cannot check a ticket in atomically; and every analytics question is a manual
pivot table. FestFlow replaces that with a database that **enforces the rules itself** and answers the
analytics questions with aggregation pipelines.

![screenshot: event list with search and filters](screenshots/07_event_list.png)

## 2. Why MongoDB

| Need | Why a document database fits |
|---|---|
| **Polymorphic events** | A pro show has an artist and up to 5 ticket tiers; a workshop has a seat limit, instructor and prerequisites; a cultural event allows free tiers. One `events` collection holds all three shapes, and the validator's `oneOf` enforces the right shape per `type` — no sparse SQL table, no table-per-type joins. |
| **Atomic stock** | Each tier's `sold` counter lives *inside* the event document next to its `capacity`. MongoDB guarantees single-document atomicity, so one `find_one_and_update` can check "are there seats?" and take them in the same step. This is the project's headline design decision (§8.4). |
| **Natural aggregates** | A booking with its tickets, a venue with its sections, a volunteer with their shifts are each read and written together, so they are embedded. |
| **Analytics in the database** | The aggregation framework (`$facet`, `$setWindowFields`, `$bucket`, `$dateTrunc` with time zones, `$lookup` with sub-pipelines) answers the dashboard questions server-side. |
| **Schema validation without losing flexibility** | `$jsonSchema` + `$expr` validators reject bad documents at the database, while the document model still allows the polymorphism above. |

## 3. Database and document design

Four collections. The embed-vs-reference reasoning is also written as a comment at the top of each
validator in `core/schemas.py`.

### 3.1 Relationship map

| Relationship | Kind | Modelled as | Why |
|---|---|---|---|
| venue → sections | 1 : few | **embedded** array in `venues` | A handful per venue, bounded, always read with the venue. |
| event → ticket tiers | 1 : few | **embedded** array in `events` | Stock must sit next to capacity for the atomic seat claim. |
| event → venue | N : 1 | **reference** `events.venueId` | Many events share a venue; venues are edited on their own. |
| volunteer → shifts | 1 : few | **embedded** array in `users` | A few shifts over 5 days, always shown with the volunteer. |
| booking → tickets | 1 : few (1–6) | **embedded** array in `bookings` | Tickets are bought, shown and cancelled together. |
| users ↔ events | **N : M** | resolved through **`bookings`** (`userId`, `eventId` references) | An event has thousands of bookings — embedding them would create unbounded arrays and huge documents; bookings are also queried on their own. |
| booking → event type | denormalized copy | `bookings.eventType` | An event's type never changes, so the copy can never go stale; analytics group by type without a `$lookup`. |

![screenshot: Compass schema view of bookings](screenshots/06_compass_schema.png)

### 3.2 Collections and sample documents

**`venues`**
```json
{ "_id": ObjectId("..."), "name": "Central Lawns", "block": "Main Campus",
  "sections": [ { "name": "Fan Pit", "capacity": 2500 }, { "name": "Gold", "capacity": 6000 },
                { "name": "General", "capacity": 12000 } ],
  "facilities": ["stage", "sound_system"], "createdAt": ISODate("2026-08-27T03:30:00Z") }
```

**`events`** (a pro show — the polymorphic shape changes with `type`)
```json
{ "_id": ObjectId("..."), "title": "Grand Finale ft. Bass Theory", "slug": "grand-finale-bass-theory",
  "description": "The closing night: Bass Theory headlines, followed by fireworks over the lawns.",
  "type": "pro_show", "venueId": ObjectId("..."),
  "startTime": ISODate("2026-10-21T14:00:00Z"), "endTime": ISODate("2026-10-21T17:30:00Z"),
  "status": "scheduled", "tags": ["music", "electronic", "pro-show", "finale"], "artist": "Bass Theory",
  "ticketTiers": [
    { "tierId": "FANPIT", "name": "Fan Pit", "section": "Fan Pit", "price": 2999, "capacity": 143, "sold": 143 },
    { "tierId": "GOLD",   "name": "Gold",    "section": "Gold",    "price": 1799, "capacity": 690, "sold": 263 },
    { "tierId": "SILVER", "name": "Silver",  "section": "General", "price": 899,  "capacity": 1270, "sold": 347 } ],
  "createdAt": ISODate("..."), "updatedAt": ISODate("...") }
```
A **workshop** has no `ticketTiers`; instead `seatLimit`, `seatsTaken`, `instructor`, `prerequisites`.

**`users`** (a volunteer — only volunteers may have shifts)
```json
{ "_id": ObjectId("..."), "name": "Demo Volunteer", "email": "volunteer@spectra.test", "phone": "9876543210",
  "role": "volunteer", "passwordHash": "pbkdf2_sha256$...", "college": "Lovely Professional University",
  "shifts": [ { "eventId": ObjectId("..."), "duty": "gate",
                "start": ISODate("2026-10-20T13:30:00Z"), "end": ISODate("2026-10-20T17:00:00Z") } ],
  "createdAt": ISODate("...") }
```

**`bookings`**
```json
{ "_id": ObjectId("..."), "userId": ObjectId("..."), "eventId": ObjectId("..."), "eventType": "pro_show",
  "status": "confirmed", "paymentMode": "UPI", "totalAmount": 1798,
  "tickets": [ { "code": "SPX-DZ1UZGAA", "tierId": "SILVER", "price": 899, "checkedInAt": null },
               { "code": "SPX-PHHLXKVM", "tierId": "SILVER", "price": 899, "checkedInAt": null } ],
  "bookedAt": ISODate("2026-10-18T18:30:00Z"), "cancelledAt": null }
```

### 3.3 BSON types used and why

| BSON type | Where | Why |
|---|---|---|
| `objectId` | every `_id`, `venueId`, `userId`, `eventId`, `shifts.eventId` | 12-byte ids generated client-side; they embed a timestamp and make references explicit. |
| `int` / `long` | prices, capacities, `sold`, `seatsTaken`, `totalAmount` | **Money is whole rupees in integers.** Doubles cannot represent many decimal amounts exactly (0.1 + 0.2 ≠ 0.3); integers sum exactly. `Decimal128` was considered and rejected: rupee prices never have paise, and integers keep `$sum`/`$inc` simple. The validator accepts only `["int", "long"]`, so a double such as `1500.0` is rejected. |
| `date` | `startTime`, `endTime`, `bookedAt`, `cancelledAt`, `checkedInAt`, shift times | Real BSON dates (UTC milliseconds) enable `$dateTrunc`, `$dateDiff`, range queries and window frames. Stored in **UTC**, displayed in **Asia/Kolkata**. |
| `null` | `cancelledAt`, `checkedInAt`, workshop `tierId` | "Not yet" — an explicit null, allowed by `bsonType: ["date", "null"]`. |
| `string` | names, codes, enums | Patterns enforce formats: `^SPX-[A-Z0-9]{8}$`, `^[a-z0-9-]+$`, `^[6-9][0-9]{9}$`. |
| `array` / `object` | tiers, sections, tickets, shifts | Embedded 1:few data (§3.1). |
| `bool` | — (derived in pipelines only) | |

## 4. Validation rules

Every collection has a validator of the form
`{ $and: [ { $jsonSchema: … }, { $expr: rule1 }, { $expr: rule2 }, … ] }`, applied with
**`validationLevel: "strict"`** (every insert *and update* is checked) and **`validationAction: "error"`**
(bad writes are rejected, not just logged). `$jsonSchema` checks the shape of one document; `$expr`
compares two fields of the same document, which JSON Schema cannot do. `additionalProperties: false`
rejects misspelt field names.

![screenshot: events validator in Compass](screenshots/03_compass_validator.png)

### 4.1 In the validator (`core/schemas.py`, mirrored in `queries/00_setup.js`)

| Collection | Rule | How |
|---|---|---|
| all | types, required fields, enums, lengths, patterns, no unknown fields | `$jsonSchema` |
| events | shape depends on `type` (pro show needs artist + 1–5 tiers with price ≥ 1; cultural 1–3 tiers, price ≥ 0; workshop has seatLimit 1–200 and **no** tiers) | `oneOf` with three branches, each pinning `type`; `not: {anyOf: [required…]}` forbids other types' fields |
| events | `endTime > startTime` | `$expr` |
| events | every tier `sold ≤ capacity` (**the safety net behind atomic booking**) | `$expr` + `$allElementsTrue` over `$map` of `ticketTiers` |
| events | workshop `seatsTaken ≤ seatLimit` | `$expr` |
| events | tier ids unique within an event (the booking update targets a tier by id) | `$expr`: `$size($setUnion(ids)) == $size(ids)` |
| venues | at least one section; section names unique | `minItems`, `$expr` |
| users | email lowercase + pattern; phone pattern; optional 8-digit `regNo` | `pattern` |
| users | only volunteers may have shifts; every shift ends after it starts | `$expr` |
| bookings | 1–6 tickets; ticket-code pattern | `minItems`/`maxItems`, `pattern` |
| bookings | `totalAmount == sum(tickets.price)` | `$expr` with `$sum: "$tickets.price"` |
| bookings | workshop booking = exactly one ticket with `tierId: null`; other bookings name a tier; one booking = one tier | `$expr` |
| bookings | ticket codes unique *within* a booking (a unique multikey index cannot stop duplicates inside one array) | `$expr` |
| bookings | `cancelledAt` set exactly when status is not `confirmed` | `$expr` |

`queries/01_validation_demo.js` inserts one valid document per collection and then attempts 14 invalid
writes, printing MongoDB's `errInfo` for each; all 14 are rejected.

![screenshot: 01_validation_demo.js output](screenshots/04_validation_demo.png)

`core/exceptions.py` turns the deeply nested `errInfo` into readable `(field, message)` pairs — e.g.
`("tickets[0].code", "regular expression did not match (got 'SPX-abc')")` — and the web forms show each
message under the matching field. `oneOf` reports every branch, so only the branch for the document's own
type is shown; `$expr` failures are named using the rule list in `core/schemas.py`.

### 4.2 In services (rules a validator cannot express)

A validator only ever sees **the one document being written**. Rules that involve other documents or
collections are enforced in `services/`, each commented with why:

| Rule | Why not in the validator | Where |
|---|---|---|
| A tier's capacity must fit its venue section | needs the **venue** document | `event_service._check_tiers_fit` |
| No two non-cancelled events overlap in a venue | needs **other event** documents | `event_service._check_no_overlap` |
| A venue cannot shrink below the tiers using it / cannot be deleted while used | needs **event** documents | `event_service` |
| A new event must start in the future | needs "now"; a validator rule would also block later updates to past events (e.g. marking them completed) | `event_service.create_event` |
| A shift must lie inside its event's window | needs the **event** document | `volunteer_service.assign_shift` |
| A volunteer's shifts must not overlap | needs the **other shifts** — enforced atomically in the `$push` filter | `users_repo.push_shift` |
| One workshop seat per person | needs the user's **other bookings** | `booking_service._book_workshop` |
| Cancel only before start and before any check-in | needs the **event** start time | `booking_service.cancel` (check-in condition is in the update filter) |

`python manage.py verify_data` (and `queries/05_verify_data.js`) re-checks all cross-document invariants
with aggregation pipelines — e.g. every tier's `sold` equals its confirmed tickets — and the seed command
runs it automatically.

## 5. CRUD operations

| Operation | PyMongo call | mongosh equivalent | Where used |
|---|---|---|---|
| Create one | `insert_one` | `insertOne` | `users_repo.insert_user` (sign-up), `events_repo.insert_event`, `venues_repo.insert_venue`, `bookings_repo.insert_booking` |
| Create many | `insert_many(ordered=False)` | `insertMany` | `seed` (venues, events, users) |
| Bulk write | `bulk_write([InsertOne…])` | `bulkWrite` | `seed` (bookings) |
| Read with filter + projection + sort + skip/limit | `find(q, proj).sort().skip().limit()` | same | `events_repo.list_events` (paginated event list) |
| Read one | `find_one` | `findOne` | `events_repo.get_event`, `users_repo.get_user_with_password` |
| Count | `count_documents` | `countDocuments` | pager totals, `bookings_repo.count_for_event` (before hard delete) |
| Text search | `find({$text: {$search}}, {score: {$meta: "textScore"}}).sort(score)` | same | `events_repo.search_events` |
| Distinct | `distinct` | `distinct` | `events_repo.list_start_times`, `bookings_repo.distinct_user_ids` |
| Update fields | `update_one` + `$set` | `updateOne` | `venues_repo.update_venue`, `events_repo.update_event_fields` |
| Increment | `$inc` (+ positional `$`) | same | `events_repo.claim_tier_seats` / `release_tier_seats` |
| Array filters | `update_one(…, array_filters=[{"t.tierId": …}])` | `{arrayFilters: […]}` | `events_repo.set_tier_price`, `set_tier_capacity` |
| All-positional | `"ticketTiers.$[].sold": 0` | same | `events_repo.reset_stock` (event cancelled) |
| Push / pull | `$push`, `$pull` | same | `users_repo.push_shift` / `pull_shift`; `events_repo.add_tier` / `remove_tier` |
| Atomic read-modify-write | `find_one_and_update(…, return_document=AFTER)` | `findOneAndUpdate` | `events_repo.claim_tier_seats`, `bookings_repo.check_in_ticket` |
| Update many | `update_many` (pipeline update with `$cond`) | `updateMany` | `bookings_repo.refund_all_for_event`; `users_repo.pull_shifts_for_event` |
| Upsert | `update_one(…, {$set, $setOnInsert}, upsert=True)` | `{upsert: true}` | `venues_repo.upsert_venue_by_name` (idempotent `import_venues`) |
| Delete one | `delete_one` | `deleteOne` | `venues_repo.delete_venue`, `events_repo.delete_event` |
| Delete many | `delete_many` | `deleteMany` | `users_repo.delete_inactive_attendees` |

Every repository function has a twin with the same name in `queries/02_crud.js`, which runs them all on
demo documents and cleans up after itself.

## 6. Aggregation pipelines

All pipelines live in `repositories/analytics_repo.py` (one function each, with a docstring giving the
business question and the reason for every stage) and in `queries/03_aggregations.js` under the same
names. A test runs both and asserts **identical results, including BSON number types**. Sample outputs
below are from the default seed (5,000 bookings).

1. **`revenue_by_event_and_tier`** — *How much did each tier earn?* `$match` confirmed → `$unwind` tickets →
   `$group` by (event, tier) → `$lookup` events (sub-pipeline projecting only title/type) → `$unwind` →
   `$project` → `$sort`. Grouping **before** the join means ~40 lookups instead of ~9,000.
   *Sample:* Bollywood Night · GOLD ₹8,22,951 (549 tickets); Bollywood Night · FANPIT ₹7,24,710 (290).
2. **`occupancy_per_tier`** — *How full is each tier, and how big is its section?* `$match` ticketed,
   non-cancelled → `$unwind` tiers → `$lookup` venues with **`let` + pipeline** that `$unwind`s sections and
   `$match`es the tier's section with `$expr` → `occupancyPct` with `$round`.
   *Sample:* Alfaaz Poetry Slam · Free Pass 170/170 (section 250) = 100.0%.
3. **`checkin_traffic_by_hour`** — *Gate traffic per hour (IST)?* `$match` bookings with **some** scanned
   ticket (`$elemMatch` — see §8.5) → `$unwind` → `$match` scanned → `$group` by
   `$dateTrunc {unit: "hour", timezone: "Asia/Kolkata"}`. *Sample:* 16 Oct 18:00 IST — 1,278 check-ins
   (the hour before the opening pro show).
4. **`no_show_rate_per_event`** — *Who did not turn up?* `$match` confirmed → `$unwind` → `$group` per event
   counting checked-in tickets with `$cond` → `$lookup` status → `$match` completed → rate.
   *Sample:* Drone Building 101 46.8%; Bollywood Night 11.9%.
5. **`booking_lead_time_buckets`** — *How far ahead do people book?* Runs on `events` and `$lookup`s bookings
   through the `eventId` index (25 indexed lookups instead of 5,000) → `$dateDiff` days (IST) → `$bucket`
   with boundaries `[0,1,3,7,14,30]` and default `"30+"`. *Sample:* same day 860 · 1–2 days 1,339 ·
   3–6 days 1,307 · 1–2 weeks 745 · 2–4 weeks 549 · 30+ days 200.
6. **`top_spenders`** — `$group` per user → **`$setWindowFields` with `$rank`** → `$limit 10` → `$lookup`
   users. *Sample:* 1. Dev Sibal (Chandigarh University) ₹33,723 over 12 bookings.
7. **`revenue_by_payment_mode`** — `$group` per mode → `$setWindowFields` with an **unbounded window** puts
   the grand total on every row → share %. *Sample:* UPI 69.4%, CARD 12.5%, CASH 10.6%, NETBANKING 7.5%.
8. **`volunteer_hours`** + **`coverage_per_event`** — `$unwind` embedded shifts → `$dateDiff` minutes →
   `$group` per volunteer; coverage uses two `$group`s and `$arrayToObject` to pivot duties into columns.
   *Sample:* Demo Volunteer 4 shifts, 10.2 h; Alfaaz: {gate 2, help_desk 1, logistics 1}.
9. **`cancellation_rate_by_event_type`** — `$group` by the **denormalized `eventType`** (no `$lookup`) with
   conditional sums. *Sample:* cultural 13.3%, pro show 7.4%, workshop 6.2%.
10. **`daily_sales_with_moving_avg`** — `$group` per IST day (`$dateTrunc`) → `$setWindowFields` with a
    **range window** `[-2, 0]` days (date-based, so a day without sales is not silently skipped).
    *Sample:* 16 Oct 1,492 tickets, 3-day average 1,115.0.
11. **`organizer_dashboard`** — one **`$facet`** returns `kpis`, `salesByDay`, `topEvents` and
    `paymentModes` in a single round trip. *Sample KPIs:* 5,000 bookings, 8,997 tickets, revenue ₹68,92,129,
    5,435 check-ins, 8.9% cancelled.
12. **`noshow_features`** — the ML feature pipeline (§9).

The **event search** uses the weighted text index (`title` 5, `tags` 3, `description` 1) with
`{$meta: "textScore"}`; searching "dance" ranks *Street Dance Battle* (6.33) above *Nritya: Classical Dance
Showcase* (6.125) and *Bhangra Clash* (3.0).

![screenshot: organizer dashboard](screenshots/13_dashboard.png)
![screenshot: Query Lab showing a named pipeline and its results](screenshots/18_query_lab.png)

## 7. Indexing and explain results

| Collection | Index | Serves |
|---|---|---|
| venues | `{name: 1}` unique | venue lookup, idempotent import |
| events | `{slug: 1}` unique | event URLs |
| events | `{startTime: 1}` | "what's on next" |
| events | `{type: 1, startTime: 1}` | filter by type, sorted by time (equality field first, sort field second — the ESR rule) |
| events | text `{title, description, tags}` weights 5/1/3 | search box (a collection may have only one text index) |
| users | `{email: 1}` unique | login, duplicate sign-ups |
| users | `{role: 1}` | volunteer lists |
| bookings | `{eventId: 1, status: 1}` | per-event analytics, refunds, joins from events |
| bookings | `{userId: 1, bookedAt: -1}` | "My bookings", newest first |
| bookings | `{"tickets.code": 1}` unique (multikey) | gate check-in; no code in two bookings |

`python manage.py explain_report` drops each index, runs `explain("executionStats")`, recreates it and runs
again (`queries/04_indexes_explain.js` does the same in mongosh). On **50,000 bookings**:

| Query | Before (no index) | After (index) |
|---|---|---|
| bookings of one event, status confirmed | COLLSCAN · docs examined **50,000** · 50 ms | IXSCAN · 9,420 keys / 9,420 docs · 23 ms |
| cultural events sorted by start | `SORT > COLLSCAN` · 25 docs | `FETCH > IXSCAN` · 11 docs, **no in-memory SORT** |
| search "dance" | regex `COLLSCAN` · 25 docs | `TEXT_MATCH > FETCH > IXSCAN` · 3 docs |
| my bookings, newest first | `SORT > COLLSCAN` · **50,000** docs · 50 ms | IXSCAN · **5** docs · 0 ms |
| gate check-in by ticket code | COLLSCAN · **50,000** docs · 87 ms | `EXPRESS_IXSCAN` · **1** doc · 0 ms |

Notes: `$text` cannot run at all without a text index (error 27, IndexNotFound), so the "before" row for
search is the regex you would write instead. `EXPRESS_IXSCAN` is MongoDB 8.0's fast path for an equality
match on a unique index.

**Covered query.** `find({eventId, status: "confirmed"}, {_id: 0, eventId: 1, status: 1})` is answered from
the `{eventId, status}` index alone: plan `PROJECTION_COVERED > IXSCAN`, **`totalDocsExamined: 0`**, versus
9,420 documents read for the same filter returning whole documents. Full tables: `docs/explain_results.md`.

![screenshot: explain_report output](screenshots/21_explain_report.png)

## 8. PyMongo implementation

### 8.1 Client lifecycle
`core/db.py` creates **one `MongoClient` per process**, lazily and under a lock. A client is a thread-safe
connection *pool* plus monitoring threads, so it is shared by all request threads; creating one per
request would open new sockets and threads every time. It is created with `tz_aware=True` and
`tzinfo=timezone.utc`, so every datetime read back is timezone-aware UTC, and with
`serverSelectionTimeoutMS=5000` so pages fail fast when the server is down.

### 8.2 Layering
```
web (views, forms, templates)  ->  services (business rules)  ->  repositories (ONLY place PyMongo is called)  ->  MongoDB
```
Views never import PyMongo or repositories; services never import PyMongo (they use `bson.ObjectId` only
as an id type). Admin commands in `core/management/commands` (`init_db`, `seed`, `verify_data`,
`explain_report`) talk to the database directly because they are database-administration tools, not
request handling. Django is used **only** for URLs, views, templates, forms, sessions and CSRF:
`DATABASES = {}`, no ORM, no `models.py`, no `django.contrib.auth/admin/contenttypes/sessions`; sessions
and flash messages live in signed cookies; login is our own check against `users` with Django's
`make_password` / `check_password` (which work without the auth app).

### 8.3 Error handling
Repositories wrap writes in `mongo_errors()`, which translates PyMongo errors into application exceptions:
code **121** → `ValidationFailed` (with readable problems), code **11000** → `DuplicateError` (with the
clashing field), so views never import PyMongo exceptions. `find_one_and_update` raises a plain
`OperationFailure` (it runs the `findAndModify` command), so the translator catches the parent class and
checks the code.

### 8.4 Atomic booking (no overselling)
```python
events.find_one_and_update(
    {"_id": event_id, "status": "scheduled", "startTime": {"$gt": now},
     "ticketTiers": {"$elemMatch": {"tierId": tier_id, "sold": {"$lte": capacity - qty}}}},
    {"$inc": {"ticketTiers.$.sold": qty}, "$set": {"updatedAt": now}},
    return_document=ReturnDocument.AFTER)
```
The filter only matches if seats remain, and the `$inc` happens in the same single-document operation, so
no other request can slip in between. `None` → `SoldOutError`. `$elemMatch` cannot compare two fields of
the same element (`sold` vs `capacity`), so the service reads `capacity` first; if an organizer lowers it
meanwhile, the validator's `sold ≤ capacity` rule rejects the `$inc` — the database-level safety net.
Workshops use `$expr: {$lt: ["$seatsTaken", "$seatLimit"]}` because those are top-level fields.

Then the booking is inserted. **Why compensation instead of a transaction:** the seat claim (events) and the
booking (bookings) are two documents; making them all-or-nothing would need a multi-document transaction,
which requires a replica set (our dev server is a single node). The critical step is already atomic; if
the insert fails, the service gives the seats back with `$inc: -qty`. A small race with event cancellation
is closed by re-reading the event after the insert. In production we would use a transaction (§10).

**Proof:** a test starts 50 threads buying the last 10 seats: exactly 10 succeed, `sold == 10`, 10 bookings.

### 8.5 Other write patterns
- **Check-in:** one `find_one_and_update` matching `{"status": "confirmed", "tickets": {"$elemMatch":
  {"code": code, "checkedInAt": None}}}` and setting `tickets.$.checkedInAt`; a second scan no longer
  matches, so a ticket is never counted twice (a test fires 20 simultaneous scans: exactly 1 succeeds).
  Only on a miss does a follow-up read distinguish *invalid*, *already used at …* and *cancelled*.
- **Guards in the filter, not in Python:** capacity never below `sold`, remove a tier only while unsold,
  no overlapping shift (`$not: {$elemMatch: …}` in the `$push` filter), cancel only if no ticket is
  scanned — check and write are one atomic step.
- **Soft delete:** cancelling an event sets `status: "cancelled"` first (so no new claim can match), then
  one `update_many` with a **pipeline update** sets each booking to `refunded` or `cancelled` via `$cond` on
  its own `totalAmount`, then `ticketTiers.$[].sold` is reset. Hard delete is allowed only with zero bookings.
- **Arrays and `$ne`:** `{"tickets.checkedInAt": {"$ne": null}}` on an array means "*no* element is null",
  not "some element is not null"; the tests caught this in an early version of the check-in pipeline, which
  now uses `$elemMatch`.

### 8.6 Testing
`python -m pytest` runs **135 tests** against a separate `spectra_test` database: 25 raw validator
rejections (WriteError 121), the overselling and workshop races, compensation, cancellation, check-in,
every pipeline against a hand-built fixture with paper-checkable numbers, cross-document rules, unique
indexes, Query Lab guards, web flows, and **Python vs mongosh parity** (the `queries/*.js` files are run
with mongosh and must give the same validators, indexes and aggregation results).

![screenshot: pytest run](screenshots/24_pytest.png)

## 9. Predictive analytics (optional): no-show predictor

The **`noshow_features`** aggregation builds one row per confirmed booking: lead time, ticket price and
count, event type, event hour (IST), and the user's **history** — number of earlier completed bookings and
their no-show rate — computed with `$setWindowFields` over the window `["unbounded", -1]` (strictly earlier
rows), so a booking's own outcome never leaks into its features. `python manage.py train_noshow` fits a
scaled `LogisticRegression` on bookings of completed events (stratified 75/25 split):

| Metric | Value |
|---|---|
| labelled bookings | 3,365 (no-show rate 18.0%) |
| accuracy | 0.818 — the same as always guessing "shows up" (0.819) |
| ROC-AUC | **0.664** — the model ranks risk better than chance |

The dashboard shows each upcoming event's expected no-show % (paid pro shows ≈ 12%, free workshops and
cultural events ≈ 24–40%).

**Honest caveat.** The data is synthetic, and the seed script decides no-shows from exactly these signals
(free/cheap tickets, early bookings, a hidden per-user "flakiness"). The model therefore mostly *relearns
the patterns the seed injected*. It demonstrates the MongoDB → features → model pipeline; it is **not** a
finding about real festival-goers. Accuracy is reported next to its baseline precisely because it would
otherwise look better than it is.

![screenshot: predicted no-shows card](screenshots/27_forecast_card.png)

## 10. Production practices and limitations

| Area | Development setup | What production needs |
|---|---|---|
| **Authentication** | MongoDB without auth (Docker, localhost only) | Enable SCRAM authentication, a least-privilege application user (readWrite on one database), a separate admin user, TLS. |
| **Topology** | single node | A **replica set** (3 members) for failover — which also enables **multi-document transactions**: the seat claim + booking insert would be one transaction instead of compensation. |
| **Backups** | none | Scheduled `mongodump` or (on Atlas) continuous cloud backups with point-in-time restore; test restores. |
| **Hosting** | local / Docker | **MongoDB Atlas** gives replica sets, backups, monitoring, alerts and the Performance Advisor (index suggestions) out of the box. |
| **Secrets** | `.env` file | A secret manager; strong `DJANGO_SECRET_KEY`; `DEBUG = False`; HTTPS-only cookies. |
| **Validation changes** | `collMod` re-applies validators | `collMod` does not re-check existing documents: run `verify_data` (or `{$nor: [validator]}` queries) after every schema change. |
| **Query Lab** | organizer-only, read-only allowlist, `maxTimeMS` 5000, 200 rows | Restrict further (or remove) in production; run it against a secondary / analytics node. |
| **Analytics load** | pipelines run on the primary | Read preference `secondaryPreferred` or an Atlas analytics node for heavy dashboards; cache dashboard results. |

Known limitations: hard-deleting an event counts its bookings and then deletes — a booking landing in
between would be orphaned (a transaction would close it; `verify_data` would flag it). Payments are
simulated (no gateway). Times use IST throughout; a multi-timezone event would need per-venue time zones.

## 11. Conclusion

FestFlow shows how a document database fits an operational problem with hard consistency needs. Embedding
the ticket stock in the event document lets a single atomic update guarantee "never oversell" — proven by a
50-thread test — while `$jsonSchema` + `$expr` validators keep every document honest at the database level,
and services handle only the rules that genuinely span documents. Thirteen aggregation pipelines (with
mongosh twins that return identical results) turn the same collections into a live dashboard, the indexes
cut the gate check-in from scanning 50,000 documents to reading one, and a `$setWindowFields` feature
pipeline feeds a small, honestly-evaluated predictive model.

---

**Appendix — reproducing everything**
```bash
python manage.py init_db                        # validators + indexes
python manage.py seed --reset                   # 5,000 bookings + verify_data
mongosh spectra queries/01_validation_demo.js   # 14 rejected writes
mongosh spectra queries/02_crud.js              # every repository query
mongosh spectra queries/03_aggregations.js      # every pipeline
python manage.py seed --reset --bookings 50000 && python manage.py explain_report
python manage.py train_noshow                   # optional ML
python -m pytest                                # 135 tests
python manage.py runserver                      # http://127.0.0.1:8000/
```
