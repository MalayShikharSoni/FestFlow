"""
Static, hand-written data for the seed command: venues, the 25-event schedule,
colleges and instructor names. Kept apart from seed.py so the generation logic
stays short and the "catalogue" is easy to read and edit.

The schedule is hand-assigned so that no two events share a venue at the same
time (the overlap rule from §5.2); verify_data checks this after seeding.
"""
from typing import Any

# --- Venues ------------------------------------------------------------------
# key -> venue fields. Section names are what event tiers refer to.
VENUES: dict[str, dict[str, Any]] = {
    "lawns": {
        "name": "Central Lawns",
        "block": "Main Campus",
        "sections": [("Fan Pit", 2500), ("Gold", 6000), ("General", 12000)],
        "facilities": ["stage", "sound_system"],
    },
    "arena": {
        "name": "Sports Complex Arena",
        "block": "Sports Block",
        "sections": [("Floor", 4000), ("Stands", 6000)],
        "facilities": ["stage", "sound_system", "wheelchair_access", "green_room"],
    },
    "auditorium": {
        "name": "Shanti Devi Auditorium",
        "block": "Block 32",
        "sections": [("Floor", 1200), ("Balcony", 600)],
        "facilities": ["projector", "sound_system", "stage", "ac", "wheelchair_access", "green_room"],
    },
    "oat": {
        "name": "Open Air Theatre",
        "block": "Block 13",
        "sections": [("Arena", 3000), ("Steps", 2000)],
        "facilities": ["stage", "sound_system", "wheelchair_access"],
    },
    "seminar": {
        "name": "Block 34 Seminar Hall",
        "block": "Block 34",
        "sections": [("Hall", 250)],
        "facilities": ["projector", "sound_system", "ac", "wheelchair_access"],
    },
    "labs": {
        "name": "Innovation Lab Complex",
        "block": "Block 38",
        "sections": [("Lab A", 120), ("Lab B", 80)],
        "facilities": ["projector", "ac"],
    },
}

# --- Events ------------------------------------------------------------------
# day: fest day 1-5. start: IST clock time. hours: duration.
# popularity: relative share of bookings. Tiers: (tierId, name, section, price, weight)
# where weight is the relative popularity of the tier within its event.
EVENTS: list[dict[str, Any]] = [
    # ---------------- Day 1 ----------------
    {"day": 1, "start": "10:00", "hours": 3, "venue": "labs", "type": "workshop",
     "title": "Hands-on Machine Learning with Python", "slug": "hands-on-ml-python",
     "description": "Train, evaluate and tune your first classifiers with scikit-learn on real campus datasets.",
     "tags": ["tech", "ml", "python"], "popularity": 1.4,
     "instructor": "Dr. Meera Iyer", "prerequisites": ["Python basics", "Laptop with Python 3"]},
    {"day": 1, "start": "11:00", "hours": 2, "venue": "seminar", "type": "cultural",
     "title": "Alfaaz: Poetry Slam", "slug": "alfaaz-poetry-slam",
     "description": "Hindi, Urdu and English spoken-word poetry, judged live by the audience.",
     "tags": ["literary", "poetry"], "popularity": 1.6,
     "tiers": [("FREE", "Free Pass", "Hall", 0, 1)]},
    {"day": 1, "start": "14:00", "hours": 3, "venue": "labs", "type": "workshop",
     "title": "Drone Building 101", "slug": "drone-building-101",
     "description": "Assemble, calibrate and fly a quadcopter. Kits are provided; teams of two.",
     "tags": ["tech", "robotics", "drones"], "popularity": 1.2,
     "instructor": "Capt. Arjun Bhalla", "prerequisites": ["None"]},
    {"day": 1, "start": "15:00", "hours": 2.5, "venue": "oat", "type": "cultural",
     "title": "Nukkad Natak Face-off", "slug": "nukkad-natak-face-off",
     "description": "Street-theatre troupes from twelve colleges take on social issues in twenty-minute acts.",
     "tags": ["theatre", "street-play"], "popularity": 3.0,
     "tiers": [("FREE", "Steps (Free)", "Steps", 0, 3), ("GA", "Arena Seating", "Arena", 99, 2)]},
    {"day": 1, "start": "19:00", "hours": 3.5, "venue": "lawns", "type": "pro_show",
     "title": "Bollywood Night ft. Aria Sen", "slug": "bollywood-night-aria-sen",
     "description": "The opening pro show: chart-topping Bollywood hits live with a twelve-piece band.",
     "tags": ["music", "bollywood", "pro-show"], "popularity": 12,
     "artist": "Aria Sen",
     "tiers": [("FANPIT", "Fan Pit", "Fan Pit", 2499, 2), ("GOLD", "Gold", "Gold", 1499, 3),
               ("SILVER", "Silver", "General", 699, 5)]},
    # ---------------- Day 2 ----------------
    {"day": 2, "start": "10:00", "hours": 3, "venue": "seminar", "type": "workshop",
     "title": "MongoDB Aggregation Bootcamp", "slug": "mongodb-aggregation-bootcamp",
     "description": "From $match to $setWindowFields: build analytics pipelines on a fest ticketing dataset.",
     "tags": ["tech", "databases", "mongodb"], "popularity": 1.1,
     "instructor": "Prof. Kavita Rao", "prerequisites": ["Basic JSON", "Laptop with mongosh"]},
    {"day": 2, "start": "10:30", "hours": 3, "venue": "labs", "type": "workshop",
     "title": "Arduino and IoT Hack Lab", "slug": "arduino-iot-hack-lab",
     "description": "Wire sensors to an Arduino, push readings to the cloud and build a live dashboard.",
     "tags": ["tech", "iot", "electronics"], "popularity": 1.0,
     "instructor": "Er. Sandeep Gill", "prerequisites": ["Basic C programming"]},
    {"day": 2, "start": "14:00", "hours": 3, "venue": "auditorium", "type": "cultural",
     "title": "Nritya: Classical Dance Showcase", "slug": "nritya-classical-dance",
     "description": "Kathak, Bharatanatyam and Odissi performances by university and guest ensembles.",
     "tags": ["dance", "classical"], "popularity": 2.2,
     "tiers": [("GA", "Balcony", "Balcony", 149, 3), ("VIP", "Floor", "Floor", 299, 2)]},
    {"day": 2, "start": "16:00", "hours": 2.5, "venue": "oat", "type": "cultural",
     "title": "Street Dance Battle", "slug": "street-dance-battle",
     "description": "Hip-hop, krump and popping crews battle it out in knockout rounds.",
     "tags": ["dance", "hip-hop"], "popularity": 2.8,
     "tiers": [("GA", "Arena Seating", "Arena", 99, 3), ("FREE", "Steps (Free)", "Steps", 0, 2)]},
    {"day": 2, "start": "19:30", "hours": 3.5, "venue": "lawns", "type": "pro_show",
     "title": "EDM Night ft. DJ Kabir Vale", "slug": "edm-night-kabir-vale",
     "description": "Lasers, CO2 jets and a three-hour EDM set under the open sky.",
     "tags": ["music", "edm", "pro-show"], "popularity": 11,
     "artist": "DJ Kabir Vale",
     "tiers": [("FANPIT", "Fan Pit", "Fan Pit", 1999, 2), ("GOLD", "Gold", "Gold", 1199, 3),
               ("SILVER", "Silver", "General", 599, 5)]},
    # ---------------- Day 3 ----------------
    {"day": 3, "start": "10:00", "hours": 2, "venue": "seminar", "type": "workshop",
     "title": "Public Speaking Lab", "slug": "public-speaking-lab",
     "description": "Practise impromptu talks and get feedback on voice, pacing and body language.",
     "tags": ["soft-skills", "speaking"], "popularity": 0.9,
     "instructor": "Ms. Ritu Malhotra", "prerequisites": ["None"]},
    {"day": 3, "start": "11:00", "hours": 3, "venue": "labs", "type": "workshop",
     "title": "Photography Masterclass", "slug": "photography-masterclass",
     "description": "Composition, low-light shooting and editing in Lightroom, with a campus photo walk.",
     "tags": ["photography", "art"], "popularity": 1.3,
     "instructor": "Mr. Farhan Qureshi", "prerequisites": ["Any camera or phone"]},
    {"day": 3, "start": "14:30", "hours": 2.5, "venue": "auditorium", "type": "cultural",
     "title": "Rangmanch Theatre Fest", "slug": "rangmanch-theatre-fest",
     "description": "Three one-act plays, from farce to tragedy, staged by inter-college drama societies.",
     "tags": ["theatre", "drama"], "popularity": 2.0,
     "tiers": [("GA", "Balcony", "Balcony", 99, 3), ("VIP", "Floor", "Floor", 199, 2)]},
    {"day": 3, "start": "17:00", "hours": 2, "venue": "oat", "type": "cultural",
     "title": "Bhangra Clash", "slug": "bhangra-clash",
     "description": "High-energy Bhangra and Giddha teams compete for the Spectra trophy.",
     "tags": ["dance", "folk", "punjabi"], "popularity": 3.2,
     "tiers": [("FREE", "Steps (Free)", "Steps", 0, 2), ("GA", "Arena Seating", "Arena", 99, 3)]},
    {"day": 3, "start": "19:30", "hours": 3, "venue": "arena", "type": "pro_show",
     "title": "Sufi Night ft. Nooran Echoes", "slug": "sufi-night-nooran-echoes",
     "description": "An evening of qawwali and Sufi rock at the Sports Complex Arena.",
     "tags": ["music", "sufi", "pro-show"], "popularity": 9,
     "artist": "Nooran Echoes",
     "tiers": [("FLOOR", "Floor", "Floor", 1299, 2), ("STANDS", "Stands", "Stands", 599, 3)]},
    # ---------------- Day 4 ----------------
    {"day": 4, "start": "10:00", "hours": 3, "venue": "labs", "type": "workshop",
     "title": "Ethical Hacking Basics", "slug": "ethical-hacking-basics",
     "description": "Capture-the-flag style introduction to web vulnerabilities in a sandboxed lab.",
     "tags": ["tech", "security"], "popularity": 1.5,
     "instructor": "Mr. Vikram Sethi", "prerequisites": ["Linux command line basics"]},
    {"day": 4, "start": "11:00", "hours": 2, "venue": "seminar", "type": "workshop",
     "title": "Startup Pitch Clinic", "slug": "startup-pitch-clinic",
     "description": "Refine your pitch deck and rehearse in front of founders and angel investors.",
     "tags": ["entrepreneurship", "startups"], "popularity": 0.8,
     "instructor": "Ms. Nandini Kapoor", "prerequisites": ["A startup idea"]},
    {"day": 4, "start": "14:00", "hours": 2.5, "venue": "oat", "type": "cultural",
     "title": "Battle of Bands", "slug": "battle-of-bands",
     "description": "Eight college rock bands, twenty minutes each, one winner.",
     "tags": ["music", "rock", "competition"], "popularity": 3.5,
     "tiers": [("GA", "Arena Seating", "Arena", 149, 3), ("FREE", "Steps (Free)", "Steps", 0, 2)]},
    {"day": 4, "start": "15:00", "hours": 2, "venue": "auditorium", "type": "cultural",
     "title": "Runway Riot: Fashion Show", "slug": "runway-riot-fashion-show",
     "description": "Student designers showcase sustainable streetwear collections on the runway.",
     "tags": ["fashion", "design"], "popularity": 2.4, "cancelled": True,
     "tiers": [("GA", "Balcony", "Balcony", 199, 3), ("VIP", "Floor", "Floor", 399, 2)]},
    {"day": 4, "start": "19:00", "hours": 3.5, "venue": "lawns", "type": "pro_show",
     "title": "Rock Night ft. The Monsoon Project", "slug": "rock-night-monsoon-project",
     "description": "Indie rock anthems and a surprise acoustic set from The Monsoon Project.",
     "tags": ["music", "rock", "pro-show"], "popularity": 10,
     "artist": "The Monsoon Project",
     "tiers": [("FANPIT", "Fan Pit", "Fan Pit", 1799, 2), ("GOLD", "Gold", "Gold", 999, 3),
               ("SILVER", "Silver", "General", 499, 5)]},
    # ---------------- Day 5 ----------------
    {"day": 5, "start": "10:00", "hours": 3, "venue": "labs", "type": "workshop",
     "title": "UI/UX Design Sprint", "slug": "ui-ux-design-sprint",
     "description": "Go from user interviews to a clickable Figma prototype in one morning.",
     "tags": ["design", "ux"], "popularity": 1.2,
     "instructor": "Ms. Ananya Bose", "prerequisites": ["Free Figma account"]},
    {"day": 5, "start": "12:00", "hours": 2, "venue": "seminar", "type": "cultural",
     "title": "Open Mic: Unplugged", "slug": "open-mic-unplugged",
     "description": "Acoustic music, stand-up and storytelling. Sign up at the door for a five-minute slot.",
     "tags": ["music", "open-mic", "comedy"], "popularity": 1.8,
     "tiers": [("FREE", "Free Pass", "Hall", 0, 1)]},
    {"day": 5, "start": "15:00", "hours": 2.5, "venue": "auditorium", "type": "cultural",
     "title": "Stand-up Comedy Night", "slug": "stand-up-comedy-night",
     "description": "Four rising comics roast campus life, hostel food and the 8 am lecture.",
     "tags": ["comedy", "stand-up"], "popularity": 3.0,
     "tiers": [("GA", "Balcony", "Balcony", 199, 3), ("VIP", "Floor", "Floor", 349, 2)]},
    {"day": 5, "start": "16:00", "hours": 2, "venue": "oat", "type": "cultural",
     "title": "Mr and Ms Spectra", "slug": "mr-and-ms-spectra",
     "description": "Talent, wit and stage presence: the fest's flagship personality contest.",
     "tags": ["pageant", "talent"], "popularity": 2.0,
     "tiers": [("FREE", "Steps (Free)", "Steps", 0, 2), ("GA", "Arena Seating", "Arena", 99, 2)]},
    {"day": 5, "start": "19:30", "hours": 3.5, "venue": "lawns", "type": "pro_show",
     "title": "Grand Finale ft. Bass Theory", "slug": "grand-finale-bass-theory",
     "description": "The closing night: Bass Theory headlines, followed by fireworks over the lawns.",
     "tags": ["music", "electronic", "pro-show", "finale"], "popularity": 14,
     "artist": "Bass Theory",
     "tiers": [("FANPIT", "Fan Pit", "Fan Pit", 2999, 2), ("GOLD", "Gold", "Gold", 1799, 3),
               ("SILVER", "Silver", "General", 899, 5)]},
]

# The seed forces this tier to be exactly sold out, so the demo can show a SoldOutError.
DEMO_SOLD_OUT_TIER = ("grand-finale-bass-theory", "FANPIT")

# --- People ------------------------------------------------------------------
HOME_COLLEGE = "Lovely Professional University"
# (college, weight). The host university dominates; LPU students also get a regNo.
COLLEGES: list[tuple[str, float]] = [
    (HOME_COLLEGE, 70),
    ("Chitkara University", 6),
    ("Thapar Institute of Engineering and Technology", 5),
    ("Guru Nanak Dev University", 5),
    ("Panjab University", 4),
    ("Chandigarh University", 4),
    ("DAV University Jalandhar", 3),
    ("NIT Jalandhar", 3),
]

EMAIL_DOMAINS: list[tuple[str, float]] = [("gmail.com", 70), ("outlook.com", 12), ("yahoo.co.in", 10),
                                          ("rediffmail.com", 3), ("proton.me", 5)]

# (mode, weight) for paid bookings; free bookings always use "FREE".
PAYMENT_MODES: list[tuple[str, float]] = [("UPI", 68), ("CARD", 14), ("CASH", 10), ("NETBANKING", 8)]

# Tickets per booking for ticketed events: (quantity, weight). Workshops are always 1.
QUANTITIES: list[tuple[int, float]] = [(1, 40), (2, 33), (3, 12), (4, 9), (5, 3), (6, 3)]

# Lead time (days between booking and event start), skewed toward last-minute.
# (low_days, high_days, weight); a value is drawn uniformly inside the chosen bucket.
LEAD_TIME_BUCKETS: list[tuple[float, float, float]] = [
    (0.02, 1, 34), (1, 3, 26), (3, 7, 18), (7, 14, 11), (14, 30, 8), (30, 45, 3),
]

DUTIES: list[tuple[str, float]] = [("gate", 35), ("help_desk", 25), ("logistics", 25), ("stage", 15)]

DEMO_PASSWORD = "Spectra@2026"
DEMO_ACCOUNTS = [
    {"name": "Spectra Organizer", "email": "organizer@spectra.test", "role": "organizer"},
    {"name": "Demo Attendee", "email": "attendee@spectra.test", "role": "attendee"},
    {"name": "Demo Volunteer", "email": "volunteer@spectra.test", "role": "volunteer"},
]
