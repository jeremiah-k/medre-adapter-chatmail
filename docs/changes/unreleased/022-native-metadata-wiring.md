# Wire supplied native metadata into Chatmail evidence

Record the native facts the Delta Chat contact snapshot already supplies instead
of dropping them. Admitted events now carry the contact's email address
(`sender_address`) in the native namespace, and attribution projects it as
`source_sender_handle` — the platform's actual identity — degrading to the
profile-scoped transport ID when no usable address exists. Native send and
receive times are recorded as `timestamp_sent` and `timestamp_received`, and
connection setup keeps the validated conversation facts (`chat_type`,
`chat_name`, `chat_protected`) on every admitted event. Malformed or absent
values are omitted, never guessed.

Delivery feedback notes are now state-differentiated: state 26 (`OutDelivered`)
reports outgoing SMTP-server acceptance with recipient delivery and reading
unconfirmed, while state 28 (`OutMdnRcvd`)
reports reading as confirmed by a native MDN (read receipt); both remain
`remote_service` SMTP-acceptance evidence and imply no relay acceptance or
bridge confidentiality. A bounded opted-in `public_facts` projection (currently
`encrypted_and_signed`) becomes the single source of chatmail facts for core's
transport-neutral bridge context. Capabilities stay unchanged:
`metadata_fields` remains false because the adapter has no native channel that
transmits structured metadata fields, and `identity_encryption` remains false
with its rationale documented on the provider contract page.
