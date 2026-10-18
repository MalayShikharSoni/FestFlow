// 00_setup.js — mongosh twin of core/schemas.py + core/indexes.py + `manage.py init_db`.
//
// Run:  docker exec -it festflow-mongo mongosh spectra /queries/00_setup.js
//   or: mongosh spectra queries/00_setup.js
//
// Idempotent, exactly like init_db: createCollection if missing, collMod if present,
// and indexes are (re)built. Python and JS must produce identical validators —
// change both files together.
//
// Number types: the validators accept only int/long for money and counters.
// mongosh sends a whole number that fits in 32 bits (500) as int, but a fraction
// (1500.5) or a larger number (3000000000) as double, which is rejected.
// Our scripts write NumberInt(500) / NumberLong(...) so the type is explicit.

const VALIDATION_LEVEL = "strict";
const VALIDATION_ACTION = "error";

const EVENT_TYPES = ["pro_show", "cultural", "workshop"];
const EVENT_STATUSES = ["scheduled", "cancelled", "completed"];
const FACILITIES = ["projector", "sound_system", "stage", "ac", "wheelchair_access", "green_room"];
const ROLES = ["attendee", "volunteer", "organizer"];
const DUTIES = ["gate", "stage", "help_desk", "logistics"];
const BOOKING_STATUSES = ["confirmed", "cancelled", "refunded"];
const PAYMENT_MODES = ["UPI", "CARD", "NETBANKING", "CASH", "FREE"];

// --- Reusable building blocks (same names as the Python helpers) -------------
const INT = ["int", "long"];

function string(minLen = 1, maxLen = null, extra = {}) {
  const schema = { bsonType: "string", minLength: minLen };
  if (maxLen !== null) schema.maxLength = maxLen;
  return Object.assign(schema, extra);
}

function int(minimum = null, maximum = null) {
  const schema = { bsonType: INT };
  if (minimum !== null) schema.minimum = minimum;
  if (maximum !== null) schema.maximum = maximum;
  return schema;
}

// Matches if ANY of the fields is present; used as { not: anyPresent(...) }.
function anyPresent(...fields) {
  return { anyOf: fields.map((f) => ({ required: [f] })) };
}

// $expr: values at arrayPath contain no duplicates ($setUnion drops duplicates).
function allUnique(arrayPath) {
  const values = { $ifNull: [arrayPath, []] };
  return { $eq: [{ $size: { $setUnion: [values, []] } }, { $size: values }] };
}

function buildValidator(jsonSchema, rules) {
  return { $and: [{ $jsonSchema: jsonSchema }, ...rules.map(([, expr]) => ({ $expr: expr }))] };
}

// --- venues: EMBED sections (1:few, bounded, always read with the venue) -----
const venuesSchema = {
  bsonType: "object",
  title: "venues",
  required: ["name", "block", "sections", "facilities", "createdAt"],
  additionalProperties: false,
  properties: {
    _id: { bsonType: "objectId" },
    name: string(3, 80),
    block: string(1, 40),
    sections: {
      bsonType: "array",
      minItems: 1,
      maxItems: 10,
      items: {
        bsonType: "object",
        required: ["name", "capacity"],
        additionalProperties: false,
        properties: { name: string(1, 40), capacity: int(1) },
      },
    },
    facilities: { bsonType: "array", uniqueItems: true, items: { enum: FACILITIES } },
    createdAt: { bsonType: "date" },
  },
};

const venuesRules = [
  ["section names must be unique within a venue", allUnique("$sections.name")],
];

// --- events: polymorphic by `type`. EMBED ticketTiers (atomic stock), REFERENCE venue (N:1)
function tierSchema(minPrice) {
  return {
    bsonType: "object",
    required: ["tierId", "name", "section", "price", "capacity", "sold"],
    additionalProperties: false,
    properties: {
      tierId: string(2, 10, { pattern: "^[A-Z]{2,10}$" }),
      name: string(1, 40),
      section: string(1, 40),
      price: int(minPrice),
      capacity: int(1),
      sold: int(0),
    },
  };
}

const TICKETED_ONLY_FIELDS = ["ticketTiers", "artist"];
const WORKSHOP_ONLY_FIELDS = ["seatLimit", "seatsTaken", "instructor", "prerequisites"];

const proShowShape = {
  properties: {
    type: { enum: ["pro_show"] },
    ticketTiers: { bsonType: "array", minItems: 1, maxItems: 5, items: tierSchema(1) },
    artist: string(1, 80),
  },
  required: ["ticketTiers", "artist"],
  not: anyPresent(...WORKSHOP_ONLY_FIELDS),
};

const culturalShape = {
  properties: {
    type: { enum: ["cultural"] },
    // price >= 0: free passes are allowed for cultural events.
    ticketTiers: { bsonType: "array", minItems: 1, maxItems: 3, items: tierSchema(0) },
  },
  required: ["ticketTiers"],
  not: anyPresent("artist", ...WORKSHOP_ONLY_FIELDS),
};

const workshopShape = {
  properties: {
    type: { enum: ["workshop"] },
    seatLimit: int(1, 200),
    seatsTaken: int(0),
    instructor: string(1, 80),
    prerequisites: { bsonType: "array", items: string(1, 120) },
  },
  required: ["seatLimit", "seatsTaken", "instructor", "prerequisites"],
  not: anyPresent(...TICKETED_ONLY_FIELDS),
};

const eventsSchema = {
  bsonType: "object",
  title: "events",
  required: ["title", "slug", "description", "type", "venueId", "startTime",
             "endTime", "status", "tags", "createdAt", "updatedAt"],
  // additionalProperties only sees properties at THIS level, so type-specific
  // fields are listed loosely here and tightened inside oneOf.
  additionalProperties: false,
  properties: {
    _id: { bsonType: "objectId" },
    title: string(3, 120),
    slug: string(1, 120, { pattern: "^[a-z0-9-]+$" }),
    description: string(10, 2000),
    type: { enum: EVENT_TYPES },
    venueId: { bsonType: "objectId" },
    startTime: { bsonType: "date" },
    endTime: { bsonType: "date" },
    status: { enum: EVENT_STATUSES },
    tags: { bsonType: "array", items: string(1, 30) },
    createdAt: { bsonType: "date" },
    updatedAt: { bsonType: "date" },
    ticketTiers: { bsonType: "array" },
    artist: { bsonType: "string" },
    seatLimit: { bsonType: INT },
    seatsTaken: { bsonType: INT },
    instructor: { bsonType: "string" },
    prerequisites: { bsonType: "array" },
  },
  oneOf: [proShowShape, culturalShape, workshopShape],
};

const eventsRules = [
  ["endTime must be after startTime", { $gt: ["$endTime", "$startTime"] }],
  // Safety net behind atomic booking: no tier can ever be oversold.
  ["every tier must have sold <= capacity", { $allElementsTrue: [{ $map: {
    input: { $ifNull: ["$ticketTiers", []] },
    as: "t",
    in: { $lte: ["$$t.sold", "$$t.capacity"] },
  } }] }],
  ["workshop seatsTaken must be <= seatLimit", { $or: [
    { $ne: ["$type", "workshop"] },
    { $lte: ["$seatsTaken", "$seatLimit"] },
  ] }],
  ["tierIds must be unique within an event", allUnique("$ticketTiers.tierId")],
];

// --- users: EMBED shifts (1:few) ---------------------------------------------
const usersSchema = {
  bsonType: "object",
  title: "users",
  required: ["name", "email", "phone", "role", "passwordHash", "college", "shifts", "createdAt"],
  additionalProperties: false,
  properties: {
    _id: { bsonType: "objectId" },
    name: string(2, 80),
    email: string(5, 120, { pattern: "^[a-z0-9._%+-]+@[a-z0-9.-]+\\.[a-z]{2,}$" }),
    phone: string(10, 10, { pattern: "^[6-9][0-9]{9}$" }),
    role: { enum: ROLES },
    passwordHash: string(1, 200),
    college: string(2, 120),
    regNo: string(8, 8, { pattern: "^[0-9]{8}$" }), // optional
    shifts: {
      bsonType: "array",
      maxItems: 20,
      items: {
        bsonType: "object",
        required: ["eventId", "duty", "start", "end"],
        additionalProperties: false,
        properties: {
          eventId: { bsonType: "objectId" },
          duty: { enum: DUTIES },
          start: { bsonType: "date" },
          end: { bsonType: "date" },
        },
      },
    },
    createdAt: { bsonType: "date" },
  },
};

const usersRules = [
  ["only volunteers may have shifts", { $or: [
    { $eq: ["$role", "volunteer"] },
    { $eq: [{ $size: { $ifNull: ["$shifts", []] } }, 0] },
  ] }],
  ["every shift must end after it starts", { $allElementsTrue: [{ $map: {
    input: { $ifNull: ["$shifts", []] },
    as: "s",
    in: { $gt: ["$$s.end", "$$s.start"] },
  } }] }],
];

// --- bookings: REFERENCE user + event (N:M); eventType denormalized ---------
const bookingsSchema = {
  bsonType: "object",
  title: "bookings",
  required: ["userId", "eventId", "eventType", "status", "paymentMode",
             "tickets", "totalAmount", "bookedAt", "cancelledAt"],
  additionalProperties: false,
  properties: {
    _id: { bsonType: "objectId" },
    userId: { bsonType: "objectId" },
    eventId: { bsonType: "objectId" },
    eventType: { enum: EVENT_TYPES },
    status: { enum: BOOKING_STATUSES },
    paymentMode: { enum: PAYMENT_MODES },
    tickets: {
      bsonType: "array",
      minItems: 1,
      maxItems: 6,
      items: {
        bsonType: "object",
        required: ["code", "tierId", "price", "checkedInAt"],
        additionalProperties: false,
        properties: {
          code: string(12, 12, { pattern: "^SPX-[A-Z0-9]{8}$" }),
          tierId: { bsonType: ["string", "null"] },
          price: int(0),
          checkedInAt: { bsonType: ["date", "null"] },
        },
      },
    },
    totalAmount: int(0),
    bookedAt: { bsonType: "date" },
    cancelledAt: { bsonType: ["date", "null"] },
  },
};

const bookingsRules = [
  ["totalAmount must equal the sum of ticket prices",
    { $eq: ["$totalAmount", { $sum: "$tickets.price" }] }],
  ["a workshop booking has exactly one ticket with tierId null", { $or: [
    { $ne: ["$eventType", "workshop"] },
    { $and: [
      { $eq: [{ $size: "$tickets" }, 1] },
      { $eq: [{ $arrayElemAt: ["$tickets.tierId", 0] }, null] },
    ] },
  ] }],
  ["tickets for a ticketed event must each name a tier", { $or: [
    { $eq: ["$eventType", "workshop"] },
    { $not: [{ $in: [null, "$tickets.tierId"] }] },
  ] }],
  ["all tickets in a booking must share one tier",
    { $eq: [{ $size: { $setUnion: ["$tickets.tierId", []] } }, 1] }],
  // A unique multikey index cannot stop the same code twice INSIDE one booking.
  ["ticket codes must be unique within a booking", allUnique("$tickets.code")],
  ["cancelledAt is set exactly when status is not confirmed", { $eq: [
    { $eq: ["$status", "confirmed"] },
    { $eq: [{ $ifNull: ["$cancelledAt", null] }, null] },
  ] }],
];

const VALIDATORS = {
  venues: buildValidator(venuesSchema, venuesRules),
  events: buildValidator(eventsSchema, eventsRules),
  users: buildValidator(usersSchema, usersRules),
  bookings: buildValidator(bookingsSchema, bookingsRules),
};

// --- Indexes (core/indexes.py) -----------------------------------------------
const INDEXES = {
  venues: [
    [{ name: 1 }, { name: "venues_name_unique", unique: true }],
  ],
  events: [
    [{ slug: 1 }, { name: "events_slug_unique", unique: true }],
    [{ startTime: 1 }, { name: "events_startTime" }],
    [{ type: 1, startTime: 1 }, { name: "events_type_startTime" }],
    [{ title: "text", description: "text", tags: "text" },
      { name: "events_text", weights: { title: 5, tags: 3, description: 1 }, default_language: "english" }],
  ],
  users: [
    [{ email: 1 }, { name: "users_email_unique", unique: true }],
    [{ role: 1 }, { name: "users_role" }],
  ],
  bookings: [
    [{ eventId: 1, status: 1 }, { name: "bookings_eventId_status" }],
    [{ userId: 1, bookedAt: -1 }, { name: "bookings_userId_bookedAt" }],
    [{ "tickets.code": 1 }, { name: "bookings_ticketCode_unique", unique: true }],
  ],
};

const INDEX_CONFLICT_CODES = [85, 86]; // IndexOptionsConflict, IndexKeySpecsConflict

// --- Apply -------------------------------------------------------------------
print(`Initialising database '${db.getName()}'`);

const existing = db.getCollectionNames();
for (const [name, validator] of Object.entries(VALIDATORS)) {
  const options = { validator, validationLevel: VALIDATION_LEVEL, validationAction: VALIDATION_ACTION };
  if (existing.includes(name)) {
    db.runCommand({ collMod: name, ...options });
    print(`  ${name}: validator updated (collMod)`);
  } else {
    db.createCollection(name, options);
    print(`  ${name}: collection created with validator`);
  }
}

for (const [collName, specs] of Object.entries(INDEXES)) {
  for (const [keys, options] of specs) {
    try {
      db.getCollection(collName).createIndex(keys, options);
      print(`  ${collName}.${options.name}: ok`);
    } catch (e) {
      if (!INDEX_CONFLICT_CODES.includes(e.code)) throw e;
      db.getCollection(collName).dropIndex(options.name);
      db.getCollection(collName).createIndex(keys, options);
      print(`  ${collName}.${options.name}: rebuilt (definition changed)`);
    }
  }
}

print("Done.");
