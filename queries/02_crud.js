// 02_crud.js — mongosh twins of every PyMongo call in repositories/*.py.
//
// Run on a seeded database:
//   docker exec -it festflow-mongo mongosh spectra /queries/02_crud.js
//   or: mongosh spectra queries/02_crud.js
//
// Every "--- <repo>.<function>" header names the Python function that runs the
// same query. Reads use the seeded data; writes only touch demo documents
// (venue "CRUD Demo Hall", events "crud-demo-*", users "crud.*@spectra.test")
// which are removed at the end, so the seeded data is left as it was.
//
// CRUD coverage (where each operation is used in the Python code):
//   insertOne ............ venues_repo.insert_venue, events_repo.insert_event, users_repo.insert_user, bookings_repo.insert_booking
//   insertMany/bulkWrite . seed.insert_documents (core/management/commands/seed.py)
//   find + projection + sort + skip/limit ... events_repo.list_events
//   findOne .............. events_repo.get_event, users_repo.get_user, ...
//   countDocuments ....... events_repo.count_events, bookings_repo.count_for_event, ...
//   $text + textScore .... events_repo.search_events
//   distinct ............. events_repo.list_start_times, bookings_repo.distinct_user_ids
//   updateOne $set ....... venues_repo.update_venue, events_repo.update_event_fields
//   $inc + positional $ .. events_repo.claim_tier_seats / release_tier_seats
//   arrayFilters ......... events_repo.set_tier_price / set_tier_capacity
//   all-positional $[] ... events_repo.reset_stock
//   $push / $pull ........ users_repo.push_shift / pull_shift, events_repo.add_tier / remove_tier
//   findOneAndUpdate ..... events_repo.claim_tier_seats, bookings_repo.check_in_ticket
//   updateMany ........... bookings_repo.refund_all_for_event (pipeline update), users_repo.pull_shifts_for_event
//   upsert ............... venues_repo.upsert_venue_by_name
//   deleteOne ............ venues_repo.delete_venue, events_repo.delete_event
//   deleteMany ........... users_repo.delete_inactive_attendees

const now = new Date();
const days = (n) => new Date(now.getTime() + n * 86400 * 1000);
const hours = (from, n) => new Date(from.getTime() + n * 3600 * 1000);

function show(label, value) {
  print(`\n--- ${label}`);
  printjson(value);
}

function cleanup() {
  const demoEvents = db.events.find({ slug: /^crud-demo-/ }, { _id: 1 }).toArray().map((e) => e._id);
  db.bookings.deleteMany({ eventId: { $in: demoEvents } });
  db.events.deleteMany({ _id: { $in: demoEvents } });
  db.users.deleteMany({ email: /^crud\./ });
  db.venues.deleteMany({ name: /^CRUD Demo/ });
}
cleanup();

// Same as events_repo.LIST_PROJECTION
const LIST_PROJECTION = {
  title: 1, slug: 1, type: 1, venueId: 1, startTime: 1, endTime: 1,
  status: 1, tags: 1, artist: 1, ticketTiers: 1, seatLimit: 1, seatsTaken: 1,
};

// =============================================================================
print("\n=================== venues_repo ===================");

show("venues_repo.list_venues", db.venues.find({}).sort({ name: 1 }).toArray().map((v) => v.name));

const oat = db.venues.findOne({ name: "Open Air Theatre" });
show("venues_repo.get_venue", db.venues.findOne({ _id: oat._id }, { name: 1, sections: 1 }));
show("venues_repo.get_venue_by_name", db.venues.findOne({ name: "Central Lawns" }, { name: 1, block: 1 }));
show("venues_repo.get_venues_by_ids",
  db.venues.find({ _id: { $in: [oat._id] } }, { name: 1, block: 1, sections: 1 }).toArray());

const demoVenueId = db.venues.insertOne({
  name: "CRUD Demo Hall",
  block: "Block 99",
  sections: [{ name: "Main", capacity: NumberInt(300) }],
  facilities: ["projector"],
  createdAt: now,
}).insertedId;
show("venues_repo.insert_venue", demoVenueId);

show("venues_repo.update_venue",
  db.venues.updateOne({ _id: demoVenueId }, { $set: { facilities: ["projector", "ac"] } }));

// Upsert twice: the first call inserts (upsertedId set), the second only updates.
// $setOnInsert writes createdAt only on the insert.
for (const capacity of [200, 250]) {
  show(`venues_repo.upsert_venue_by_name (capacity ${capacity})`, db.venues.updateOne(
    { name: "CRUD Demo Annex" },
    { $set: { block: "Block 99", sections: [{ name: "Room", capacity: NumberInt(capacity) }], facilities: [] },
      $setOnInsert: { createdAt: now } },
    { upsert: true }));
}

show("venues_repo.delete_venue", db.venues.deleteOne({ name: "CRUD Demo Annex" }));

// =============================================================================
print("\n=================== events_repo ===================");

// build_list_filter(event_type="pro_show") + list_events(skip=0, limit=9)
const listFilter = { type: "pro_show", status: { $ne: "cancelled" } };
show("events_repo.list_events (pro_show, page 1)",
  db.events.find(listFilter, LIST_PROJECTION).sort({ startTime: 1 }).skip(0).limit(9).toArray()
    .map((e) => `${e.startTime.toISOString()}  ${e.title}`));
show("events_repo.count_events", db.events.countDocuments(listFilter));

const textFilter = { status: { $ne: "cancelled" }, $text: { $search: "dance" } };
show("events_repo.search_events ('dance')",
  db.events.find(textFilter, { ...LIST_PROJECTION, score: { $meta: "textScore" } })
    .sort({ score: { $meta: "textScore" }, startTime: 1 }).skip(0).limit(9).toArray()
    .map((e) => ({ title: e.title, score: e.score })));
show("events_repo.count_search", db.events.countDocuments(textFilter));

const finale = db.events.findOne({ slug: "grand-finale-bass-theory" });
show("events_repo.get_event", db.events.findOne({ _id: finale._id }, { title: 1, status: 1 }));
show("events_repo.get_event_by_slug", db.events.findOne({ slug: "bhangra-clash" }, { title: 1, startTime: 1 }));
show("events_repo.get_events_by_ids",
  db.events.find({ _id: { $in: [finale._id] } }, LIST_PROJECTION).toArray().map((e) => e.title));
show("events_repo.list_all_events (count)", db.events.find({}, LIST_PROJECTION).sort({ startTime: 1 }).toArray().length);
show("events_repo.list_start_times", db.events.distinct("startTime", { status: { $ne: "cancelled" } }).length + " distinct start times");

// Overlap: starts before the other ends AND ends after the other starts.
show("events_repo.count_overlapping (finale's own slot)", db.events.countDocuments({
  venueId: finale.venueId, status: { $ne: "cancelled" },
  startTime: { $lt: finale.endTime }, endTime: { $gt: finale.startTime },
  _id: { $ne: finale._id },
}));
show("events_repo.count_for_venue (Open Air Theatre)", db.events.countDocuments({ venueId: oat._id }));
show("events_repo.list_active_for_venue (Open Air Theatre)",
  db.events.find({ venueId: oat._id, status: { $ne: "cancelled" } }, { title: 1, ticketTiers: 1 }).toArray()
    .map((e) => e.title));

// --- writes on demo events ---
const demoStart = days(60);
const demoEventId = db.events.insertOne({
  title: "CRUD Demo Night",
  slug: "crud-demo-night",
  description: "Demo event created by 02_crud.js.",
  type: "cultural",
  venueId: demoVenueId,
  startTime: demoStart,
  endTime: hours(demoStart, 3),
  status: "scheduled",
  tags: ["demo"],
  createdAt: now,
  updatedAt: now,
  ticketTiers: [{ tierId: "GA", name: "General", section: "Main", price: NumberInt(99),
                  capacity: NumberInt(10), sold: NumberInt(0) }],
}).insertedId;
show("events_repo.insert_event", demoEventId);

show("events_repo.update_event_fields",
  db.events.updateOne({ _id: demoEventId }, { $set: { tags: ["demo", "crud"], updatedAt: now } }));

// THE atomic seat claim: check (sold <= capacity - qty) and take ($inc) in one step.
// capacity = 10, qty = 4  ->  match only if sold <= 6.
show("events_repo.claim_tier_seats (4 of 10)", db.events.findOneAndUpdate(
  { _id: demoEventId, status: "scheduled", startTime: { $gt: now },
    ticketTiers: { $elemMatch: { tierId: "GA", sold: { $lte: 10 - 4 } } } },
  { $inc: { "ticketTiers.$.sold": NumberInt(4) }, $set: { updatedAt: now } },
  { returnDocument: "after", projection: { ticketTiers: 1 } }));
show("events_repo.claim_tier_seats (8 more: no match -> null = sold out)", db.events.findOneAndUpdate(
  { _id: demoEventId, status: "scheduled", startTime: { $gt: now },
    ticketTiers: { $elemMatch: { tierId: "GA", sold: { $lte: 10 - 8 } } } },
  { $inc: { "ticketTiers.$.sold": NumberInt(8) }, $set: { updatedAt: now } },
  { returnDocument: "after" }));

show("events_repo.release_tier_seats (give 1 back)", db.events.updateOne(
  { _id: demoEventId, "ticketTiers.tierId": "GA" },
  { $inc: { "ticketTiers.$.sold": NumberInt(-1) }, $set: { updatedAt: now } }));

show("events_repo.set_tier_price (arrayFilters)", db.events.updateOne(
  { _id: demoEventId, "ticketTiers.tierId": "GA" },
  { $set: { "ticketTiers.$[t].price": NumberInt(149), updatedAt: now } },
  { arrayFilters: [{ "t.tierId": "GA" }] }));

// Guard in the filter: matchedCount 0 when the new capacity is below what is sold (3).
for (const capacity of [2, 20]) {
  show(`events_repo.set_tier_capacity (${capacity})`, db.events.updateOne(
    { _id: demoEventId, ticketTiers: { $elemMatch: { tierId: "GA", sold: { $lte: capacity } } } },
    { $set: { "ticketTiers.$[t].capacity": NumberInt(capacity), updatedAt: now } },
    { arrayFilters: [{ "t.tierId": "GA" }] }));
}

show("events_repo.add_tier ($push VIP)", db.events.updateOne(
  { _id: demoEventId, "ticketTiers.tierId": { $ne: "VIP" } },
  { $push: { ticketTiers: { tierId: "VIP", name: "VIP", section: "Main", price: NumberInt(299),
                            capacity: NumberInt(5), sold: NumberInt(0) } },
    $set: { updatedAt: now } }));
show("events_repo.remove_tier ($pull VIP, allowed: sold 0)", db.events.updateOne(
  { _id: demoEventId, ticketTiers: { $elemMatch: { tierId: "VIP", sold: 0 } } },
  { $pull: { ticketTiers: { tierId: "VIP" } }, $set: { updatedAt: now } }));
show("events_repo.remove_tier ($pull GA, refused: sold 3 -> matchedCount 0)", db.events.updateOne(
  { _id: demoEventId, ticketTiers: { $elemMatch: { tierId: "GA", sold: 0 } } },
  { $pull: { ticketTiers: { tierId: "GA" } }, $set: { updatedAt: now } }));

const demoWorkshopId = db.events.insertOne({
  title: "CRUD Demo Workshop",
  slug: "crud-demo-workshop",
  description: "Demo workshop created by 02_crud.js.",
  type: "workshop",
  venueId: demoVenueId,
  startTime: days(61),
  endTime: hours(days(61), 2),
  status: "scheduled",
  tags: [],
  createdAt: now,
  updatedAt: now,
  seatLimit: NumberInt(1),
  seatsTaken: NumberInt(0),
  instructor: "Dr. Demo",
  prerequisites: [],
}).insertedId;

// Top-level fields: $expr can compare seatsTaken with seatLimit directly.
const claimWorkshop = () => db.events.findOneAndUpdate(
  { _id: demoWorkshopId, type: "workshop", status: "scheduled", startTime: { $gt: now },
    $expr: { $lt: ["$seatsTaken", "$seatLimit"] } },
  { $inc: { seatsTaken: NumberInt(1) }, $set: { updatedAt: now } },
  { returnDocument: "after", projection: { seatsTaken: 1, seatLimit: 1 } });
show("events_repo.claim_workshop_seat (1 of 1)", claimWorkshop());
show("events_repo.claim_workshop_seat (full -> null)", claimWorkshop());
show("events_repo.release_workshop_seat", db.events.updateOne(
  { _id: demoWorkshopId, seatsTaken: { $gt: 0 } },
  { $inc: { seatsTaken: NumberInt(-1) }, $set: { updatedAt: now } }));
show("events_repo.set_seat_limit (5)", db.events.updateOne(
  { _id: demoWorkshopId, seatsTaken: { $lte: 5 } },
  { $set: { seatLimit: NumberInt(5), updatedAt: now } }));

// =============================================================================
print("\n=================== bookings_repo ===================");

const attendee = db.users.findOne({ email: "attendee@spectra.test" });
show("bookings_repo.list_for_user (newest first)",
  db.bookings.find({ userId: attendee._id }).sort({ bookedAt: -1 }).skip(0).limit(50).toArray()
    .map((b) => `${b.bookedAt.toISOString()}  ${b.status}  ${b.totalAmount}`));
const someBooking = db.bookings.findOne({ userId: attendee._id });
show("bookings_repo.get_booking", db.bookings.findOne({ _id: someBooking._id }, { status: 1, totalAmount: 1 }));
show("bookings_repo.find_by_ticket_code",
  db.bookings.findOne({ "tickets.code": someBooking.tickets[0].code }, { status: 1, "tickets.code": 1 }));
show("bookings_repo.has_confirmed_booking", db.bookings.countDocuments(
  { userId: attendee._id, eventId: finale._id, status: "confirmed" }, { limit: 1 }) === 1);
show("bookings_repo.count_for_event (finale)", db.bookings.countDocuments({ eventId: finale._id }));
show("bookings_repo.distinct_user_ids (count)", db.bookings.distinct("userId").length);

function demoBooking(codeSuffix, qty) {
  const tickets = [];
  for (let i = 0; i < qty; i++) {
    tickets.push({ code: `SPX-CRUD${codeSuffix}${i}`, tierId: "GA", price: NumberInt(149), checkedInAt: null });
  }
  return {
    userId: attendee._id, eventId: demoEventId, eventType: "cultural", status: "confirmed",
    paymentMode: "UPI", tickets, totalAmount: NumberInt(149 * qty), bookedAt: now, cancelledAt: null,
  };
}
const bookingA = db.bookings.insertOne(demoBooking("00A", 2)).insertedId;
const bookingB = db.bookings.insertOne(demoBooking("00B", 1)).insertedId;
show("bookings_repo.insert_booking", [bookingA, bookingB]);

// Atomic check-in: matches only an unused ticket of a confirmed booking.
const checkIn = (code) => db.bookings.findOneAndUpdate(
  { status: "confirmed", tickets: { $elemMatch: { code, checkedInAt: null } } },
  { $set: { "tickets.$.checkedInAt": new Date() } },
  { returnDocument: "after", projection: { tickets: 1 } });
show("bookings_repo.check_in_ticket (first scan)", checkIn("SPX-CRUD00A0"));
show("bookings_repo.check_in_ticket (second scan -> null)", checkIn("SPX-CRUD00A0"));

// Only if still confirmed and NO ticket checked in.
const NO_TICKET_CHECKED_IN = { tickets: { $not: { $elemMatch: { checkedInAt: { $ne: null } } } } };
for (const id of [bookingA, bookingB]) {
  show(`bookings_repo.cancel_booking (${id === bookingA ? "A: has a check-in -> 0" : "B: allowed -> 1"})`,
    db.bookings.updateOne(
      { _id: id, userId: attendee._id, status: "confirmed", ...NO_TICKET_CHECKED_IN },
      { $set: { status: "refunded", cancelledAt: now } }));
}

// Pipeline update: the new status is computed from each document's own totalAmount.
show("bookings_repo.refund_all_for_event", db.bookings.updateMany(
  { eventId: demoEventId, status: "confirmed" },
  [{ $set: { status: { $cond: [{ $gt: ["$totalAmount", 0] }, "refunded", "cancelled"] }, cancelledAt: now } }]));

// Back in events_repo: cancel the demo event and zero its stock.
show("events_repo.mark_cancelled", db.events.updateOne(
  { _id: demoEventId, status: "scheduled" }, { $set: { status: "cancelled", updatedAt: now } }));
show("events_repo.reset_stock (all-positional $[])", db.events.updateOne(
  { _id: demoEventId }, { $set: { "ticketTiers.$[].sold": NumberInt(0), updatedAt: now } }));
show("events_repo.reset_stock (workshop)", db.events.updateOne(
  { _id: demoWorkshopId }, { $set: { seatsTaken: NumberInt(0), updatedAt: now } }));

// =============================================================================
print("\n=================== users_repo ===================");

show("users_repo.get_user (no passwordHash)", db.users.findOne({ _id: attendee._id }, { passwordHash: 0, shifts: 0 }));
show("users_repo.get_user_with_password (fields)", Object.keys(db.users.findOne({ email: "attendee@spectra.test" })));
show("users_repo.get_users_by_ids",
  db.users.find({ _id: { $in: [attendee._id] } }, { name: 1, email: 1, college: 1 }).toArray());
show("users_repo.list_volunteers (first 3)",
  db.users.find({ role: "volunteer" }, { name: 1, email: 1, phone: 1, shifts: 1 }).sort({ name: 1 }).limit(3)
    .toArray().map((v) => `${v.name}: ${v.shifts.length} shift(s)`));

const demoVolunteerId = db.users.insertOne({
  name: "CRUD Volunteer", email: "crud.volunteer@spectra.test", phone: "9000000001", role: "volunteer",
  passwordHash: "pbkdf2_sha256$demo", college: "Lovely Professional University", shifts: [], createdAt: now,
}).insertedId;
show("users_repo.insert_user", demoVolunteerId);

// Overlap guard in the filter: no existing shift starts before the new one ends
// and ends after it starts. The second, overlapping push matches nothing.
const pushShift = (start, end, duty) => db.users.updateOne(
  { _id: demoVolunteerId, role: "volunteer",
    shifts: { $not: { $elemMatch: { start: { $lt: end }, end: { $gt: start } } } } },
  { $push: { shifts: { eventId: demoWorkshopId, duty, start, end } } });
const wsStart = days(61);
show("users_repo.push_shift (09:00-10:00 slot)", pushShift(wsStart, hours(wsStart, 1), "gate"));
show("users_repo.push_shift (overlapping -> modifiedCount 0)", pushShift(hours(wsStart, 0.5), hours(wsStart, 2), "stage"));
show("users_repo.push_shift (next hour, fine)", pushShift(hours(wsStart, 1), hours(wsStart, 2), "help_desk"));
show("users_repo.pull_shift", db.users.updateOne(
  { _id: demoVolunteerId }, { $pull: { shifts: { eventId: demoWorkshopId, start: wsStart } } }));
show("users_repo.pull_shifts_for_event (updateMany)", db.users.updateMany(
  { "shifts.eventId": demoWorkshopId }, { $pull: { shifts: { eventId: demoWorkshopId } } }));

// deleteMany: attendees registered before the cutoff who never booked. The two demo
// accounts are dated 2000-01-01, so a 2001 cutoff removes exactly them.
db.users.insertMany([1, 2].map((n) => ({
  name: `CRUD Sleeper ${n}`, email: `crud.sleeper${n}@spectra.test`, phone: "9000000002", role: "attendee",
  passwordHash: "x", college: "Lovely Professional University", shifts: [], createdAt: new Date("2000-01-01"),
})));
show("users_repo.delete_inactive_attendees", db.users.deleteMany({
  role: "attendee",
  createdAt: { $lt: new Date("2001-01-01") },
  _id: { $nin: db.bookings.distinct("userId") },
}));

// =============================================================================
print("\n=================== seed / analytics_repo extras ===================");

// seed.insert_documents: insert_many for most collections, bulk_write for bookings.
// ordered: false lets the server continue past a bad document and report them all.
show("seed.insert_documents (insertMany, ordered: false)", db.users.insertMany([1, 2].map((n) => ({
  name: `CRUD Seeded ${n}`, email: `crud.seeded${n}@spectra.test`, phone: "9000000003", role: "attendee",
  passwordHash: "x", college: "Lovely Professional University", shifts: [], createdAt: now,
})), { ordered: false }).insertedIds);
show("seed.insert_documents (bulkWrite of InsertOne, ordered: false)", db.bookings.bulkWrite([
  { insertOne: { document: demoBooking("0B1", 1) } },
  { insertOne: { document: demoBooking("0B2", 2) } },
], { ordered: false }).insertedCount);

// analytics_repo.latest_activity: "data as of" on the dashboard — newest booking first, one document.
show("analytics_repo.latest_activity",
  db.bookings.find({}, { bookedAt: 1 }).sort({ bookedAt: -1 }).limit(1).toArray()[0].bookedAt);

// =============================================================================
print("\n=================== events_repo.delete_event ===================");
db.bookings.deleteMany({ eventId: demoEventId }); // demo only: hard delete needs zero bookings
show("events_repo.delete_event", db.events.deleteOne({ _id: demoEventId }));

cleanup();
print("\nDemo documents removed.");
