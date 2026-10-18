// 03_aggregations.js — mongosh twins of repositories/analytics_repo.py (same names, same stages).
//
// Run:  docker exec -it festflow-mongo mongosh spectra /queries/03_aggregations.js
//   or: mongosh spectra queries/03_aggregations.js
// Parity mode (canonical Extended JSON, used to prove Python and JS agree):
//   PowerShell: $env:FESTFLOW_PARITY=1; mongosh --quiet spectra queries/03_aggregations.js
//   bash:       FESTFLOW_PARITY=1 mongosh --quiet spectra queries/03_aggregations.js
//
// All dates are stored in UTC; grouping by hour/day passes timezone "Asia/Kolkata".

const IST = "Asia/Kolkata";
const confirmedTickets = { $size: "$tickets" };

// 1. Revenue per event and tier. $group BEFORE $lookup so we join one row per (event, tier).
const revenue_by_event_and_tier = [
  { $match: { status: "confirmed" } },
  { $unwind: "$tickets" },
  { $group: {
    _id: { eventId: "$eventId", tierId: "$tickets.tierId" },
    revenue: { $sum: "$tickets.price" },
    tickets: { $sum: 1 },
  } },
  { $lookup: {
    from: "events",
    localField: "_id.eventId",
    foreignField: "_id",
    pipeline: [{ $project: { _id: 0, title: 1, type: 1 } }],
    as: "event",
  } },
  { $unwind: "$event" },
  { $project: {
    _id: 0, eventId: "$_id.eventId", tierId: "$_id.tierId",
    title: "$event.title", type: "$event.type", revenue: 1, tickets: 1,
  } },
  { $sort: { revenue: -1, title: 1, tierId: 1 } },
];

// 2. How full is every tier? $lookup with let + pipeline picks the tier's own section.
const occupancy_per_tier = [
  { $match: { type: { $in: ["pro_show", "cultural"] }, status: { $ne: "cancelled" } } },
  { $unwind: "$ticketTiers" },
  { $lookup: {
    from: "venues",
    let: { venueId: "$venueId", section: "$ticketTiers.section" },
    pipeline: [
      { $match: { $expr: { $eq: ["$_id", "$$venueId"] } } },
      { $unwind: "$sections" },
      { $match: { $expr: { $eq: ["$sections.name", "$$section"] } } },
      { $project: { _id: 0, venue: "$name", sectionCapacity: "$sections.capacity" } },
    ],
    as: "venue",
  } },
  { $unwind: "$venue" },
  { $project: {
    _id: 0, eventId: "$_id", title: 1,
    tierId: "$ticketTiers.tierId", tierName: "$ticketTiers.name",
    venue: "$venue.venue", section: "$ticketTiers.section",
    sold: "$ticketTiers.sold", capacity: "$ticketTiers.capacity",
    sectionCapacity: "$venue.sectionCapacity",
    occupancyPct: { $round: [
      { $multiply: [{ $divide: ["$ticketTiers.sold", "$ticketTiers.capacity"] }, 100] }, 1] },
  } },
  { $sort: { occupancyPct: -1, title: 1, tierId: 1 } },
];

// 3. Gate check-ins per IST hour. Set CHECKIN_EVENT_ID to an ObjectId to filter one event.
const CHECKIN_EVENT_ID = null;
function checkin_traffic_by_hour(eventId = CHECKIN_EVENT_ID) {
  // $elemMatch = "some ticket was scanned". {"tickets.checkedInAt": {$ne: null}} would mean
  // "NO ticket is null" on an array and silently drop groups where one person did not come.
  const firstMatch = { tickets: { $elemMatch: { checkedInAt: { $ne: null } } } };
  if (eventId !== null) firstMatch.eventId = eventId;
  return [
    { $match: firstMatch },
    { $unwind: "$tickets" },
    { $match: { "tickets.checkedInAt": { $ne: null } } },
    { $group: {
      _id: { $dateTrunc: { date: "$tickets.checkedInAt", unit: "hour", timezone: IST } },
      checkIns: { $sum: 1 },
    } },
    { $sort: { _id: 1 } },
    { $project: {
      _id: 0, hour: "$_id", checkIns: 1,
      label: { $dateToString: { date: "$_id", format: "%d-%m %H:00", timezone: IST } },
    } },
  ];
}

// 4. No-show rate of completed events. $group BEFORE $lookup: ~25 joins instead of ~4,500.
const no_show_rate_per_event = [
  { $match: { status: "confirmed" } },
  { $unwind: "$tickets" },
  { $group: {
    _id: "$eventId",
    sold: { $sum: 1 },
    checkedIn: { $sum: { $cond: [{ $ne: ["$tickets.checkedInAt", null] }, 1, 0] } },
  } },
  { $lookup: {
    from: "events",
    localField: "_id",
    foreignField: "_id",
    pipeline: [{ $project: { _id: 0, title: 1, type: 1, status: 1 } }],
    as: "event",
  } },
  { $unwind: "$event" },
  { $match: { "event.status": "completed" } },
  { $project: {
    _id: 0, eventId: "$_id", title: "$event.title", type: "$event.type",
    sold: 1, checkedIn: 1,
    noShows: { $subtract: ["$sold", "$checkedIn"] },
    noShowPct: { $round: [{ $multiply: [
      { $divide: [{ $subtract: ["$sold", "$checkedIn"] }, "$sold"] }, 100] }, 1] },
  } },
  { $sort: { noShowPct: -1, title: 1 } },
];

// 5. Booking lead time buckets. Runs on EVENTS and joins bookings via the eventId index.
const booking_lead_time_buckets = [
  { $project: { startTime: 1 } },
  { $lookup: {
    from: "bookings",
    localField: "_id",
    foreignField: "eventId",
    pipeline: [{ $project: { _id: 0, bookedAt: 1, ticketCount: { $size: "$tickets" } } }],
    as: "booking",
  } },
  { $unwind: "$booking" },
  { $set: {
    leadDays: { $dateDiff: { startDate: "$booking.bookedAt", endDate: "$startTime",
                             unit: "day", timezone: IST } },
    ticketCount: "$booking.ticketCount",
  } },
  { $bucket: {
    groupBy: "$leadDays",
    boundaries: [0, 1, 3, 7, 14, 30],
    default: "30+",
    output: { bookings: { $sum: 1 }, avgTickets: { $avg: "$ticketCount" } },
  } },
  { $project: { _id: 0, bucket: "$_id", bookings: 1, avgTickets: { $round: ["$avgTickets", 2] } } },
];

// 6. Top 10 spenders: $rank via $setWindowFields; $limit BEFORE $lookup.
function top_spenders(limit = 10) {
  return [
    { $match: { status: "confirmed" } },
    { $group: { _id: "$userId", totalSpent: { $sum: "$totalAmount" }, bookings: { $sum: 1 } } },
    { $setWindowFields: { sortBy: { totalSpent: -1 }, output: { rank: { $rank: {} } } } },
    { $sort: { rank: 1, _id: 1 } },
    { $limit: limit },
    { $lookup: {
      from: "users",
      localField: "_id",
      foreignField: "_id",
      pipeline: [{ $project: { _id: 0, name: 1, college: 1 } }],
      as: "user",
    } },
    { $unwind: "$user" },
    { $project: {
      _id: 0, rank: 1, userId: "$_id", name: "$user.name", college: "$user.college",
      totalSpent: 1, bookings: 1,
    } },
  ];
}

// 7. Revenue share per payment mode: an unbounded window puts the grand total on every row.
const revenue_by_payment_mode = [
  { $match: { status: "confirmed" } },
  { $group: { _id: "$paymentMode", revenue: { $sum: "$totalAmount" }, bookings: { $sum: 1 } } },
  { $setWindowFields: { output: {
    totalRevenue: { $sum: "$revenue", window: { documents: ["unbounded", "unbounded"] } },
  } } },
  { $project: {
    _id: 0, mode: "$_id", revenue: 1, bookings: 1,
    sharePct: { $cond: [
      { $gt: ["$totalRevenue", 0] },
      { $round: [{ $multiply: [{ $divide: ["$revenue", "$totalRevenue"] }, 100] }, 1] },
      0,
    ] },
  } },
  { $sort: { revenue: -1, mode: 1 } },
];

// 8a. Rostered hours per volunteer (on users).
const volunteer_hours = [
  { $match: { role: "volunteer" } },
  { $unwind: "$shifts" },
  { $set: { minutes: { $dateDiff: { startDate: "$shifts.start", endDate: "$shifts.end", unit: "minute" } } } },
  { $group: {
    _id: "$_id", name: { $first: "$name" }, email: { $first: "$email" },
    shifts: { $sum: 1 }, minutes: { $sum: "$minutes" },
  } },
  { $project: { _id: 0, userId: "$_id", name: 1, email: 1, shifts: 1,
                hours: { $round: [{ $divide: ["$minutes", 60] }, 1] } } },
  { $sort: { hours: -1, name: 1 } },
];

// 8b. Volunteers per duty per event; $arrayToObject turns duties into columns.
const coverage_per_event = [
  { $match: { role: "volunteer" } },
  { $unwind: "$shifts" },
  { $group: { _id: { eventId: "$shifts.eventId", duty: "$shifts.duty" }, volunteers: { $sum: 1 } } },
  { $group: {
    _id: "$_id.eventId",
    total: { $sum: "$volunteers" },
    duties: { $push: { k: "$_id.duty", v: "$volunteers" } },
  } },
  { $lookup: {
    from: "events",
    localField: "_id",
    foreignField: "_id",
    pipeline: [{ $project: { _id: 0, title: 1, startTime: 1 } }],
    as: "event",
  } },
  { $unwind: "$event" },
  { $project: {
    _id: 0, eventId: "$_id", title: "$event.title", startTime: "$event.startTime",
    total: 1, byDuty: { $arrayToObject: "$duties" },
  } },
  { $sort: { startTime: 1 } },
];

// 9. Cancellation rate per event type — uses the denormalized eventType, no $lookup.
const countStatus = (status) => ({ $sum: { $cond: [{ $eq: ["$status", status] }, 1, 0] } });
const cancellation_rate_by_event_type = [
  { $group: { _id: "$eventType", bookings: { $sum: 1 },
              cancelled: countStatus("cancelled"), refunded: countStatus("refunded") } },
  { $project: {
    _id: 0, eventType: "$_id", bookings: 1, cancelled: 1, refunded: 1,
    cancellationPct: { $round: [{ $multiply: [
      { $divide: [{ $add: ["$cancelled", "$refunded"] }, "$bookings"] }, 100] }, 1] },
  } },
  { $sort: { cancellationPct: -1, eventType: 1 } },
];

// 10. Tickets per IST day with a 3-day moving average over a RANGE window.
const daily_sales_with_moving_avg = [
  { $match: { status: "confirmed" } },
  { $group: {
    _id: { $dateTrunc: { date: "$bookedAt", unit: "day", timezone: IST } },
    tickets: { $sum: confirmedTickets },
    revenue: { $sum: "$totalAmount" },
  } },
  { $setWindowFields: {
    sortBy: { _id: 1 },
    output: { movingAvg3d: { $avg: "$tickets", window: { range: [-2, 0], unit: "day" } } },
  } },
  { $project: { _id: 0, day: "$_id", tickets: 1, revenue: 1, movingAvg3d: { $round: ["$movingAvg3d", 1] } } },
  { $sort: { day: 1 } },
];

// 11. Every dashboard number in one round trip with $facet.
const confirmed = { $eq: ["$status", "confirmed"] };
const organizer_dashboard = [
  { $facet: {
    kpis: [
      { $group: {
        _id: null,
        bookings: { $sum: 1 },
        confirmed: { $sum: { $cond: [confirmed, 1, 0] } },
        ticketsSold: { $sum: { $cond: [confirmed, confirmedTickets, 0] } },
        revenue: { $sum: { $cond: [confirmed, "$totalAmount", 0] } },
        checkedIn: { $sum: { $size: { $filter: {
          input: "$tickets", cond: { $ne: ["$$this.checkedInAt", null] } } } } },
      } },
      { $project: {
        _id: 0, bookings: 1, confirmed: 1, ticketsSold: 1, revenue: 1, checkedIn: 1,
        cancellationPct: { $round: [{ $multiply: [
          { $divide: [{ $subtract: ["$bookings", "$confirmed"] }, "$bookings"] }, 100] }, 1] },
      } },
    ],
    salesByDay: [
      { $match: { status: "confirmed" } },
      { $group: { _id: { $dateTrunc: { date: "$bookedAt", unit: "day", timezone: IST } },
                  tickets: { $sum: confirmedTickets } } },
      { $sort: { _id: 1 } },
      { $project: { _id: 0, day: "$_id", tickets: 1 } },
    ],
    topEvents: [
      { $match: { status: "confirmed" } },
      { $group: { _id: "$eventId", revenue: { $sum: "$totalAmount" }, tickets: { $sum: confirmedTickets } } },
      { $sort: { revenue: -1, _id: 1 } },
      { $limit: 5 },
      { $lookup: { from: "events", localField: "_id", foreignField: "_id",
                   pipeline: [{ $project: { _id: 0, title: 1 } }], as: "event" } },
      { $unwind: "$event" },
      { $project: { _id: 0, eventId: "$_id", title: "$event.title", revenue: 1, tickets: 1 } },
    ],
    paymentModes: [
      { $match: { status: "confirmed" } },
      { $group: { _id: "$paymentMode", bookings: { $sum: 1 }, revenue: { $sum: "$totalAmount" } } },
      { $sort: { bookings: -1, _id: 1 } },
      { $project: { _id: 0, mode: "$_id", bookings: 1, revenue: 1 } },
    ],
  } },
  { $project: { kpis: { $first: "$kpis" }, salesByDay: 1, topEvents: 1, paymentModes: 1 } },
];

// 12. ML features per confirmed booking (Phase 7 no-show predictor). The user's history
// comes from a $setWindowFields window over all EARLIER rows (["unbounded", -1]),
// so a booking's own label never leaks into its features.
const noshow_features = [
  { $match: { status: { $in: ["completed", "scheduled"] } } },
  { $project: { type: 1, status: 1, startTime: 1 } },
  { $lookup: {
    from: "bookings",
    localField: "_id",
    foreignField: "eventId",
    pipeline: [
      { $match: { status: "confirmed" } },
      { $project: { userId: 1, bookedAt: 1, tickets: 1 } },
    ],
    as: "booking",
  } },
  { $unwind: "$booking" },
  { $set: {
    completed: { $eq: ["$status", "completed"] },
    anyScanned: { $anyElementTrue: [{ $map: {
      input: "$booking.tickets", as: "t", in: { $ne: ["$$t.checkedInAt", null] },
    } }] },
  } },
  { $set: {
    noShow: { $cond: ["$completed", { $cond: ["$anyScanned", 0, 1] }, null] },
    pastWeight: { $cond: ["$completed", 1, 0] },
    leadDays: { $round: [{ $divide: [
      { $dateDiff: { startDate: "$booking.bookedAt", endDate: "$startTime", unit: "hour" } }, 24] }, 2] },
    ticketPrice: { $arrayElemAt: ["$booking.tickets.price", 0] },
    ticketCount: { $size: "$booking.tickets" },
    eventHour: { $hour: { date: "$startTime", timezone: IST } },
  } },
  { $setWindowFields: {
    partitionBy: "$booking.userId",
    sortBy: { startTime: 1 },
    output: {
      pastBookings: { $sum: "$pastWeight", window: { documents: ["unbounded", -1] } },
      pastNoShows: { $sum: "$noShow", window: { documents: ["unbounded", -1] } },
    },
  } },
  { $project: {
    _id: 0,
    bookingId: "$booking._id",
    eventId: "$_id",
    userId: "$booking.userId",
    eventStatus: "$status",
    noShow: 1,
    leadDays: 1,
    ticketPrice: 1,
    ticketCount: 1,
    eventType: "$type",
    eventHour: 1,
    pastBookings: 1,
    pastNoShowRate: { $cond: [
      { $gt: ["$pastBookings", 0] },
      { $round: [{ $divide: ["$pastNoShows", "$pastBookings"] }, 3] },
      null,
    ] },
  } },
  { $sort: { eventId: 1, bookingId: 1 } },
];

// --- Run them all -------------------------------------------------------------
const PIPELINES = [
  ["revenue_by_event_and_tier", "bookings", revenue_by_event_and_tier],
  ["occupancy_per_tier", "events", occupancy_per_tier],
  ["checkin_traffic_by_hour", "bookings", checkin_traffic_by_hour()],
  ["no_show_rate_per_event", "bookings", no_show_rate_per_event],
  ["booking_lead_time_buckets", "events", booking_lead_time_buckets],
  ["top_spenders", "bookings", top_spenders()],
  ["revenue_by_payment_mode", "bookings", revenue_by_payment_mode],
  ["volunteer_hours", "users", volunteer_hours],
  ["coverage_per_event", "users", coverage_per_event],
  ["cancellation_rate_by_event_type", "bookings", cancellation_rate_by_event_type],
  ["daily_sales_with_moving_avg", "bookings", daily_sales_with_moving_avg],
  ["organizer_dashboard", "bookings", organizer_dashboard],
  ["noshow_features", "events", noshow_features],
];

const PARITY = typeof process !== "undefined" && process.env.FESTFLOW_PARITY === "1";

for (const [name, collection, pipeline] of PIPELINES) {
  const started = Date.now();
  // promoteValues: false keeps BSON number types. JavaScript has a single Number
  // type, so by default the double 100.0 from the server arrives as 100 and
  // would re-serialize as an int — only parity mode needs the exact types.
  const options = PARITY ? { maxTimeMS: 5000, promoteValues: false } : { maxTimeMS: 5000 };
  const rows = db.getCollection(collection).aggregate(pipeline, options).toArray();
  if (PARITY) {
    print(`${name}\t${EJSON.stringify(rows, { relaxed: false })}`);
    continue;
  }
  print(`\n=== ${name} (${collection}) — ${rows.length} row(s), ${Date.now() - started} ms`);
  rows.slice(0, 12).forEach((row) => print("  " + EJSON.stringify(row, { relaxed: true })));
  if (rows.length > 12) print(`  ... ${rows.length - 12} more`);
}
