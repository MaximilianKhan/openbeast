"""Package-wide constants. Changing any of these is a compatibility event;
note it in CHANGELOG.md."""

# Bumped whenever the shape of a cached value changes. Old entries are
# simply never read again and expire on their TTL.
CACHE_SCHEMA = 3

# Hook priorities. Higher runs earlier.
PRIORITY_CORE = 100
PRIORITY_EARLY = 90
PRIORITY_NORMAL = 50
PRIORITY_LATE = 10

# A worker slot is one leased CPU share on the node; each runs this many
# cooperative workers.
WORKERS_PER_SLOT = 12

JOB_ID_PREFIX = "j-"
MAX_TAGS = 16
MAX_PAYLOAD_KB = 256

DEFAULT_TENANT = "default"
