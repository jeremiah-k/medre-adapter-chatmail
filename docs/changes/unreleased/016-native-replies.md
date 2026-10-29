# Native text replies with durable scope fencing

Chatmail outbound text replies can now use Delta Chat's native `quotedMessageId` when
core binds one stored target in the exact destination and the owning profile confirms
the target in the exact chat. The durable journal binds the reference and an attributed
inline fallback to each attempt; native submission still occurs only after a persisted
uncertainty fence. Ambiguous core bindings and missing, malformed, cross-profile or
rebound targets use readable fallback, never an inferred or cross-chat quote. This does
not change SMTP receipt authority or provide native threading.

SDK-qualified independent-peer delivery remains a required release gate;
see the opt-in two-profile `tests/test_live.py` campaign.
