// 05_verify_data.js — mongosh twin of `manage.py verify_data`.
//
// Run:  docker exec -it festflow-mongo mongosh spectra /queries/05_verify_data.js
//   or: mongosh spectra queries/05_verify_data.js
//
// Each check is one query/pipeline returning the OFFENDING documents; empty = OK.
// Pipeline names match core/management/commands/verify_data.py exactly.
// Requires 00_setup.js's validators to be in place (check 0 reads them back).

// 1. events: each tier's `sold` equals its confirmed tickets and is <= capacity.
const TIER_STOCK_PIPELINE = [
  { $match: { ticketTiers: { $exists: true } } },
  { $unwind: "$ticketTiers" },
  // localField/foreignField + pipeline (MongoDB 5.0+): join on eventId, then
  // filter to this tier's confirmed tickets inside the sub-pipeline.
  { $lookup: {
    from: "bookings",
    localField: "_id",
    foreignField: "eventId",
    let: { tierId: "$ticketTiers.tierId" },
    pipeline: [
      { $match: { status: "confirmed" } },
      { $unwind: "$tickets" },
      { $match: { $expr: { $eq: ["$tickets.tierId", "$$tierId"] } } },
      { $count: "n" },
    ],
    as: "confirmed",
  } },
  { $project: {
    title: 1,
    tierId: "$ticketTiers.tierId",
    sold: "$ticketTiers.sold",
    capacity: "$ticketTiers.capacity",
    confirmedTickets: { $ifNull: [{ $first: "$confirmed.n" }, 0] },
  } },
  { $match: { $expr: { $or: [
    { $ne: ["$sold", "$confirmedTickets"] },
    { $gt: ["$sold", "$capacity"] },
  ] } } },
];

// 2. events: each workshop's seatsTaken equals its confirmed bookings and is <= seatLimit.
const WORKSHOP_SEATS_PIPELINE = [
  { $match: { type: "workshop" } },
  { $lookup: {
    from: "bookings",
    localField: "_id",
    foreignField: "eventId",
    pipeline: [{ $match: { status: "confirmed" } }, { $count: "n" }],
    as: "confirmed",
  } },
  { $project: {
    title: 1,
    seatsTaken: 1,
    seatLimit: 1,
    confirmedSeats: { $ifNull: [{ $first: "$confirmed.n" }, 0] },
  } },
  { $match: { $expr: { $or: [
    { $ne: ["$seatsTaken", "$confirmedSeats"] },
    { $gt: ["$seatsTaken", "$seatLimit"] },
  ] } } },
];

// 3. events: the venue exists and every tier fits inside its venue section (cross-collection).
const TIER_SECTION_PIPELINE = [
  { $lookup: {
    from: "venues",
    localField: "venueId",
    foreignField: "_id",
    pipeline: [{ $project: { sections: 1 } }],
    as: "venue",
  } },
  { $set: { venue: { $first: "$venue" } } },
  { $unwind: { path: "$ticketTiers", preserveNullAndEmptyArrays: true } },
  { $set: { sectionCapacity: { $first: { $map: {
    input: { $filter: {
      input: { $ifNull: ["$venue.sections", []] },
      as: "s",
      cond: { $eq: ["$$s.name", "$ticketTiers.section"] },
    } },
    as: "s",
    in: "$$s.capacity",
  } } } } },
  { $match: { $expr: { $or: [
    { $eq: [{ $type: "$venue" }, "missing"] },
    { $and: [
      { $ne: [{ $type: "$ticketTiers" }, "missing"] },
      { $or: [
        { $eq: [{ $type: "$sectionCapacity" }, "missing"] },
        { $gt: ["$ticketTiers.capacity", "$sectionCapacity"] },
      ] },
    ] },
  ] } } },
  { $project: { title: 1, tierId: "$ticketTiers.tierId", section: "$ticketTiers.section",
                capacity: "$ticketTiers.capacity", sectionCapacity: 1 } },
];

// 4. events: no two non-cancelled events overlap in the same venue (cross-document).
// Sorted by start time per venue, any overlap shows up between NEIGHBOURS.
const VENUE_OVERLAP_PIPELINE = [
  { $match: { status: { $ne: "cancelled" } } },
  { $setWindowFields: {
    partitionBy: "$venueId",
    sortBy: { startTime: 1 },
    output: {
      previousEnd: { $shift: { output: "$endTime", by: -1 } },
      previousTitle: { $shift: { output: "$title", by: -1 } },
    },
  } },
  { $match: { $expr: { $gt: ["$previousEnd", "$startTime"] } } },
  { $project: { title: 1, previousTitle: 1, startTime: 1, previousEnd: 1 } },
];

// 5. bookings: references and cross-document consistency, one reason per bad booking.
const BOOKING_CONSISTENCY_PIPELINE = [
  { $lookup: {
    from: "events",
    localField: "eventId",
    foreignField: "_id",
    pipeline: [{ $project: { type: 1, status: 1, startTime: 1 } }],
    as: "event",
  } },
  { $lookup: {
    from: "users",
    localField: "userId",
    foreignField: "_id",
    pipeline: [{ $project: { _id: 1 } }],
    as: "user",
  } },
  { $set: { event: { $first: "$event" } } },
  { $project: { problem: { $switch: {
    branches: [
      { case: { $eq: [{ $type: "$event" }, "missing"] }, then: "event does not exist" },
      { case: { $eq: [{ $size: "$user" }, 0] }, then: "user does not exist" },
      { case: { $ne: ["$eventType", "$event.type"] }, then: "eventType differs from the event's type" },
      { case: { $and: [{ $eq: ["$status", "confirmed"] }, { $eq: ["$event.status", "cancelled"] }] },
        then: "confirmed booking for a cancelled event" },
      { case: { $and: [
        { $ne: ["$status", "confirmed"] },
        { $anyElementTrue: [{ $map: { input: "$tickets", as: "t",
                                      in: { $ne: ["$$t.checkedInAt", null] } } }] },
      ] }, then: "cancelled booking has a checked-in ticket" },
      { case: { $gt: ["$bookedAt", "$$NOW"] }, then: "bookedAt is in the future" },
      // Guard against null first: null sorts below every date in BSON ordering.
      { case: { $and: [{ $ne: ["$cancelledAt", null] }, { $lt: ["$cancelledAt", "$bookedAt"] }] },
        then: "cancelled before it was booked" },
      { case: { $and: [{ $ne: ["$cancelledAt", null] }, { $gt: ["$cancelledAt", "$event.startTime"] }] },
        then: "cancelled after the event started" },
      { case: { $ne: [{ $eq: ["$totalAmount", 0] }, { $eq: ["$paymentMode", "FREE"] }] },
        then: "paymentMode FREE must match totalAmount 0" },
    ],
    default: null,
  } } } },
  { $match: { problem: { $ne: null } } },
];

// 6. users: every shift belongs to an existing event and lies inside its time window.
const SHIFT_WINDOW_PIPELINE = [
  { $match: { role: "volunteer" } },
  { $unwind: "$shifts" },
  { $lookup: {
    from: "events",
    localField: "shifts.eventId",
    foreignField: "_id",
    pipeline: [{ $project: { title: 1, startTime: 1, endTime: 1 } }],
    as: "event",
  } },
  { $set: { event: { $first: "$event" } } },
  { $match: { $expr: { $or: [
    { $eq: [{ $type: "$event" }, "missing"] },
    { $lt: ["$shifts.start", "$event.startTime"] },
    { $gt: ["$shifts.end", "$event.endTime"] },
  ] } } },
  { $project: { email: 1, eventTitle: "$event.title", start: "$shifts.start", end: "$shifts.end" } },
];

// 7. users: a volunteer's shifts never overlap (same neighbour trick as check 4).
const SHIFT_OVERLAP_PIPELINE = [
  { $match: { role: "volunteer" } },
  { $unwind: "$shifts" },
  { $setWindowFields: {
    partitionBy: "$_id",
    sortBy: { "shifts.start": 1 },
    output: { previousEnd: { $shift: { output: "$shifts.end", by: -1 } } },
  } },
  { $match: { $expr: { $gt: ["$previousEnd", "$shifts.start"] } } },
  { $project: { email: 1, start: "$shifts.start", previousEnd: 1 } },
];

// 0. Every stored document still satisfies its validator: {$nor: [validator]}
// matches the documents that do NOT. collMod never re-checks old documents.
function documentsFailingValidators() {
  const problems = [];
  for (const info of db.getCollectionInfos({ name: { $in: ["venues", "events", "users", "bookings"] } })) {
    const count = db.getCollection(info.name).countDocuments({ $nor: [info.options.validator] });
    if (count) problems.push(`${info.name}: ${count} document(s) fail the validator`);
  }
  return problems;
}

function pipelineCheck(collection, pipeline, describe) {
  return () => db.getCollection(collection).aggregate(pipeline).toArray().map(describe);
}

const CHECKS = [
  ["all documents pass their validators", documentsFailingValidators],
  ["tier sold == confirmed tickets <= capacity", pipelineCheck("events", TIER_STOCK_PIPELINE,
    (d) => `${d.title} [${d.tierId}]: sold ${d.sold}, confirmed tickets ${d.confirmedTickets}, capacity ${d.capacity}`)],
  ["workshop seatsTaken == confirmed bookings <= seatLimit", pipelineCheck("events", WORKSHOP_SEATS_PIPELINE,
    (d) => `${d.title}: seatsTaken ${d.seatsTaken}, confirmed ${d.confirmedSeats}, limit ${d.seatLimit}`)],
  ["venue exists and each tier fits its section", pipelineCheck("events", TIER_SECTION_PIPELINE,
    (d) => `${d.title} [${d.tierId}]: section '${d.section}' holds ${d.sectionCapacity ?? "nothing"}, tier capacity ${d.capacity}`)],
  ["no overlapping events in a venue", pipelineCheck("events", VENUE_OVERLAP_PIPELINE,
    (d) => `${d.title} starts before ${d.previousTitle} ends`)],
  ["bookings reference real users/events and are consistent", pipelineCheck("bookings", BOOKING_CONSISTENCY_PIPELINE,
    (d) => `booking ${d._id}: ${d.problem}`)],
  ["volunteer shifts lie inside their event", pipelineCheck("users", SHIFT_WINDOW_PIPELINE,
    (d) => `${d.email}: shift ${d.start.toISOString()} outside ${d.eventTitle ?? "a missing event"}`)],
  ["volunteer shifts do not overlap", pipelineCheck("users", SHIFT_OVERLAP_PIPELINE,
    (d) => `${d.email}: shift at ${d.start.toISOString()} overlaps the previous one`)],
];

print(`Verifying '${db.getName()}'`);
let failed = 0;
for (const [name, check] of CHECKS) {
  const problems = check();
  if (problems.length === 0) {
    print(`  OK    ${name}`);
    continue;
  }
  failed++;
  print(`  FAIL  ${name} (${problems.length} problem(s))`);
  problems.slice(0, 10).forEach((p) => print(`          ${p}`));
}
print(failed ? `${failed} check(s) failed.` : "All invariants hold.");
