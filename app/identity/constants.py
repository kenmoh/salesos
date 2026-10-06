"""Role ranks, defined once.

Ranks gate privileged routes: ``TokenData.min_role`` compares a user's highest
role rank against this map, so a role seeded with a number outside it silently
fails every rank check. That has already bitten us — owner was seeded with rank
1 while the gate expected 80, which made owner-only paths refuse the owner and
sent them through the supervisor-PIN flow instead.

Two rules follow from keeping the numbers here:

- The owner role ranks at or above every other role, because anything that
  outranks it can satisfy an owner-only gate without being the owner.
- New roles are rejected rather than clamped when they would outrank owner, so
  a caller finds out about the ceiling instead of silently getting something
  other than what they asked for.
"""

OWNER_ROLE_NAME = "owner"

ROLE_RANKS: dict[str, int] = {
    "super_admin": 100,
    "developer": 90,
    "admin": 85,
    "owner": 80,
    "moderator": 75,
    "auditor": 70,
    "manager": 60,
    "inventory": 50,
    "cashier": 40,
    "viewer": 20,
}

OWNER_RANK: int = ROLE_RANKS[OWNER_ROLE_NAME]

# An unknown role name is treated as unsatisfiable rather than as rank 0, so a
# typo in a gate cannot quietly pass.
UNKNOWN_ROLE_RANK: int = 999