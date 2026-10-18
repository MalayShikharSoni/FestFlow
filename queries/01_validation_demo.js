// 01_validation_demo.js — writes that MUST be rejected by the validators, printing errInfo.
//
// Run after 00_setup.js:
//   docker exec -it festflow-mongo mongosh spectra /queries/01_validation_demo.js
//   or: mongosh spectra queries/01_validation_demo.js
//
// First it inserts one VALID document per collection (to prove the validators are
// not simply rejecting everything), then attempts invalid writes. Every invalid
// write must fail with code 121 (DocumentValidationFailure); the unique-index
// demo must fail with 11000 (DuplicateKey). All demo documents are removed at the end.
//
// Note NumberInt(...) everywhere: the validators accept only int/long for money
// and counters. mongosh happens to send a bare 500 as int too, but 1500.5 or
// Double(1500) is a double and is rejected (see case 11).

const now = new Date();
const hours = (h) => new Date(now.getTime() + h * 3600 * 1000);

// Clean up leftovers from an interrupted earlier run.
function cleanup() {
  db.bookings.deleteMany({ "tickets.code": /^SPX-DEMO/ });
  db.events.deleteMany({ slug: /^demo-/ });
  db.users.deleteMany({ email: /^demo\./ });
  db.venues.deleteMany({ name: /^Demo / });
}
cleanup();

// --- Builders: each call returns a fresh valid document; `extra` overrides fields.
function venueDoc(extra = {}) {
  return {
    name: "Demo Auditorium",
    block: "Block 32",
    sections: [{ name: "Floor", capacity: NumberInt(800) }, { name: "Balcony", capacity: NumberInt(300) }],
    facilities: ["stage", "sound_system", "ac"],
    createdAt: now,
    ...extra,
  };
}

function tier(extra = {}) {
  return { tierId: "GOLD", name: "Gold", section: "Floor", price: NumberInt(1500),
           capacity: NumberInt(500), sold: NumberInt(0), ...extra };
}

function proShowDoc(extra = {}) {
  return {
    title: "Demo Pro Night",
    slug: "demo-pro-night",
    description: "A demo pro show used by the validation script.",
    type: "pro_show",
    venueId: new ObjectId(),
    startTime: hours(48),
    endTime: hours(51),
    status: "scheduled",
    tags: ["music", "demo"],
    createdAt: now,
    updatedAt: now,
    artist: "Demo Artist",
    ticketTiers: [tier()],
    ...extra,
  };
}

function workshopDoc(extra = {}) {
  return {
    title: "Demo ML Workshop",
    slug: "demo-ml-workshop",
    description: "A demo workshop used by the validation script.",
    type: "workshop",
    venueId: new ObjectId(),
    startTime: hours(30),
    endTime: hours(33),
    status: "scheduled",
    tags: ["tech"],
    createdAt: now,
    updatedAt: now,
    seatLimit: NumberInt(60),
    seatsTaken: NumberInt(0),
    instructor: "Dr. Demo",
    prerequisites: ["Python basics"],
    ...extra,
  };
}

function userDoc(extra = {}) {
  return {
    name: "Demo Attendee",
    email: "demo.attendee@spectra.test",
    phone: "9876543210",
    role: "attendee",
    passwordHash: "pbkdf2_sha256$demo",
    college: "Lovely Professional University",
    shifts: [],
    createdAt: now,
    ...extra,
  };
}

function ticket(n, extra = {}) {
  return { code: `SPX-DEMO000${n}`, tierId: "GOLD", price: NumberInt(1500), checkedInAt: null, ...extra };
}

function bookingDoc(extra = {}) {
  return {
    userId: new ObjectId(),
    eventId: new ObjectId(),
    eventType: "pro_show",
    status: "confirmed",
    paymentMode: "UPI",
    tickets: [ticket(1), ticket(2)],
    totalAmount: NumberInt(3000),
    bookedAt: now,
    cancelledAt: null,
    ...extra,
  };
}

// --- Helpers -----------------------------------------------------------------
let passed = 0;
let failed = 0;

function expectSuccess(label, fn) {
  fn();
  print(`ACCEPTED       ${label}`);
}

function expectError(code, label, fn) {
  try {
    fn();
    failed++;
    print(`!! UNEXPECTED  ${label} — the write was accepted`);
  } catch (e) {
    if (e.code !== code) throw e;
    passed++;
    print(`\nREJECTED (${e.code}) ${label}`);
    if (e.errInfo) printjson(e.errInfo.details);
    else print(`  ${e.message}`);
  }
}

const rejected = (label, fn) => expectError(121, label, fn);

// --- Valid documents first ---------------------------------------------------
print("=== Valid documents ===");
expectSuccess("venue", () => db.venues.insertOne(venueDoc()));
expectSuccess("pro_show event", () => db.events.insertOne(proShowDoc()));
expectSuccess("workshop event", () => db.events.insertOne(workshopDoc()));
expectSuccess("attendee user", () => db.users.insertOne(userDoc()));
expectSuccess("booking with 2 tickets", () => db.bookings.insertOne(bookingDoc()));

// --- Invalid writes ----------------------------------------------------------
print("\n=== Writes that must be rejected ===");

rejected("1. events: type not in enum", () =>
  db.events.insertOne(proShowDoc({ slug: "demo-bad-1", type: "concert" })));

rejected("2. events: negative ticket price", () =>
  db.events.insertOne(proShowDoc({ slug: "demo-bad-2", ticketTiers: [tier({ price: NumberInt(-100) })] })));

rejected("3. events: workshop carrying ticketTiers", () =>
  db.events.insertOne(workshopDoc({ slug: "demo-bad-3", ticketTiers: [tier()] })));

rejected("4. events: pro_show without artist", () => {
  const doc = proShowDoc({ slug: "demo-bad-4" });
  delete doc.artist;
  db.events.insertOne(doc);
});

rejected("5. events: endTime before startTime ($expr)", () =>
  db.events.insertOne(proShowDoc({ slug: "demo-bad-5", endTime: hours(40) })));

rejected("6. events: tier sold > capacity ($expr)", () =>
  db.events.insertOne(proShowDoc({ slug: "demo-bad-6", ticketTiers: [tier({ sold: NumberInt(501) })] })));

rejected("7. events: $inc pushing an EXISTING tier past capacity (update, not insert)", () =>
  db.events.updateOne(
    { slug: "demo-pro-night", "ticketTiers.tierId": "GOLD" },
    { $inc: { "ticketTiers.$.sold": NumberInt(501) } }));

rejected("8. bookings: 7 tickets (maxItems 6)", () => {
  const tickets = [1, 2, 3, 4, 5, 6, 7].map((n) => ticket(n, { price: NumberInt(100) }));
  db.bookings.insertOne(bookingDoc({ tickets, totalAmount: NumberInt(700) }));
});

rejected("9. bookings: ticket code breaks the SPX-XXXXXXXX pattern", () =>
  db.bookings.insertOne(bookingDoc({ tickets: [ticket(3, { code: "SPX-abc" })], totalAmount: NumberInt(1500) })));

rejected("10. bookings: totalAmount != sum of ticket prices ($expr)", () =>
  db.bookings.insertOne(bookingDoc({ tickets: [ticket(4), ticket(5)], totalAmount: NumberInt(2999) })));

rejected("11. bookings: totalAmount stored as a double, Double(1500) — bsonType is strict", () =>
  db.bookings.insertOne(bookingDoc({ tickets: [ticket(6)], totalAmount: Double(1500) })));

rejected("12. users: attendee with shifts ($expr: only volunteers)", () =>
  db.users.insertOne(userDoc({
    email: "demo.shifty@spectra.test",
    shifts: [{ eventId: new ObjectId(), duty: "gate", start: hours(1), end: hours(3) }],
  })));

rejected("13. users: uppercase email", () =>
  db.users.insertOne(userDoc({ email: "Demo.Upper@spectra.test" })));

expectError(11000, "14. users: duplicate email (unique index, not the validator)", () =>
  db.users.insertOne(userDoc()));

// --- Summary + cleanup ---------------------------------------------------------
print(`\n=== ${passed} rejected as expected, ${failed} unexpectedly accepted ===`);
cleanup();
print("Demo documents removed.");
