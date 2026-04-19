"""Test session label logic for all 24 hours."""

def get_session_label(hour: int) -> str:
    # Block 12:00-13:00 and 17:00-22:00 UTC only
    if 12 <= hour < 13 or 17 <= hour < 22:
        return "blocked"
    # Sydney: 22:00-06:00 UTC (22-23, 0-5)
    elif 22 <= hour < 24 or 0 <= hour < 6:
        return "sydney"
    # Tokyo: 00:00-08:00 UTC (overlaps with Sydney, but Sydney takes priority for 0-5)
    elif 6 <= hour < 8:
        return "tokyo"
    # London: 07:00-12:00 UTC (overlaps with Tokyo at hour 7, London takes priority)
    elif 7 <= hour < 12:
        return "london"
    # New York: 13:00-17:00 UTC
    elif 13 <= hour < 17:
        return "new_york"
    else:
        return "other"

print("Hour | Session")
print("-----|----------")
for h in range(24):
    sess = get_session_label(h)
    print(f"{h:4d} | {sess}")
