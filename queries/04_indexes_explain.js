// 04_indexes_explain.js — mongosh twin of `manage.py explain_report`.
//
// For each query: drop the index that serves it, explain("executionStats"),
// recreate the index, explain again. Then a covered query (totalDocsExamined: 0).
// Best on a big dataset:  python manage.py seed --reset --bookings 50000
//
// Run:  docker exec -it festflow-mongo mongosh spectra /queries/04_indexes_explain.js
//   or: mongosh spectra queries/04_indexes_explain.js
//
// DEV ONLY: indexes are dropped on the live database for a moment.

// The index definitions involved (same as 00_setup.js / core/indexes.py).
const INDEX_DEFS = {
  bookings_eventId_status: ["bookings", { eventId: 1, status: 1 }, {}],
  bookings_userId_bookedAt: ["bookings", { userId: 1, bookedAt: -1 }, {}],
  bookings_ticketCode_unique: ["bookings", { "tickets.code": 1 }, { unique: true }],
  events_type_startTime: ["events", { type: 1, startTime: 1 }, {}],
  events_startTime: ["events", { startTime: 1 }, {}],
  events_text: ["events", { title: "text", description: "text", tags: "text" },
    { weights: { title: 5, tags: 3, description: 1 }, default_language: "english" }],
};

function recreate(name) {
  const [coll, keys, options] = INDEX_DEFS[name];
  db.getCollection(coll).createIndex(keys, { name, ...options });
}

// Walk the winning plan and collect the numbers we report.
function summarize(result) {
  let node = result.queryPlanner.winningPlan.queryPlan || result.queryPlanner.winningPlan;
  const stages = [];
  const indexes = [];
  while (node) {
    stages.push(node.stage);
    if (node.indexName) indexes.push(node.indexName);
    node = node.inputStage || (node.inputStages ? node.inputStages[0] : null);
  }
  let winning = stages[stages.length - 1];
  if (stages.includes("COLLSCAN")) winning = "COLLSCAN";
  else if (stages.some((s) => s.startsWith("TEXT"))) winning = "TEXT";
  else if (stages.includes("IXSCAN") || stages.includes("EXPRESS_IXSCAN")) winning = "IXSCAN";
  const s = result.executionStats;
  return {
    winning,
    stages: stages.join(" > "),
    index: indexes.length ? indexes[indexes.length - 1] : "-",
    nReturned: s.nReturned,
    keysExamined: s.totalKeysExamined,
    docsExamined: s.totalDocsExamined,
    ms: s.executionTimeMillis,
  };
}

// Second, warm run is reported (the first pays for loading a freshly built index).
function explainFind(coll, filter, sort = null, projection = null) {
  const run = () => {
    let cursor = db.getCollection(coll).find(filter, projection || {});
    if (sort) cursor = cursor.sort(sort);
    return cursor.explain("executionStats");
  };
  run();
  return summarize(run());
}

const event = db.events.findOne({ slug: "bollywood-night-aria-sen" }, { _id: 1 });
const attendee = db.users.findOne({ email: "attendee@spectra.test" }, { _id: 1 });
const booking = db.bookings.find({}, { "tickets.code": 1 }).sort({ _id: 1 }).limit(1).next();
const byEvent = { eventId: event._id, status: "confirmed" };

const CASES = [
  { title: "a) bookings of one event with status confirmed", coll: "bookings",
    drop: ["bookings_eventId_status"], filter: byEvent },
  { title: "b) cultural events sorted by start time", coll: "events",
    drop: ["events_type_startTime", "events_startTime"], filter: { type: "cultural" }, sort: { startTime: 1 } },
  // $text cannot run without a text index (error 27), so "before" uses a regex search instead.
  { title: "c) search 'dance'", coll: "events", drop: ["events_text"],
    filter: { $text: { $search: "dance" } },
    filterWithoutIndex: { $or: [{ title: { $regex: "dance", $options: "i" } },
                                { description: { $regex: "dance", $options: "i" } },
                                { tags: { $regex: "dance", $options: "i" } }] } },
  { title: "d) my bookings, newest first", coll: "bookings", drop: ["bookings_userId_bookedAt"],
    filter: { userId: attendee._id }, sort: { bookedAt: -1 } },
  { title: "e) gate check-in: find a ticket code", coll: "bookings", drop: ["bookings_ticketCode_unique"],
    filter: { "tickets.code": booking.tickets[0].code } },
];

const rows = [];
for (const c of CASES) {
  let before;
  try {
    c.drop.forEach((name) => db.getCollection(c.coll).dropIndex(name));
    before = explainFind(c.coll, c.filterWithoutIndex || c.filter, c.sort);
  } finally {
    c.drop.forEach(recreate); // always put the indexes back
  }
  const after = explainFind(c.coll, c.filter, c.sort);
  rows.push({ query: c.title, run: "before", ...before });
  rows.push({ query: "", run: "after", ...after });
}
print(`\nDataset: ${db.bookings.estimatedDocumentCount()} bookings, ${db.events.estimatedDocumentCount()} events`);
console.table(rows);

// Covered query: filter + projection only on indexed fields, _id excluded.
const plain = explainFind("bookings", byEvent);
const covered = explainFind("bookings", byEvent, null, { _id: 0, eventId: 1, status: 1 });
print("\nCovered query on {eventId: 1, status: 1}:");
console.table([
  { projection: "whole document", ...plain },
  { projection: "{_id: 0, eventId: 1, status: 1}", ...covered },
]);
print(covered.docsExamined === 0 ? "-> totalDocsExamined is 0: answered from the index alone."
                                 : "-> NOT covered (documents were read).");
