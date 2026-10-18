# Profile setup and group discovery

Add explicit profile initialization, account inspection, invitation acceptance
and conversation discovery commands. Keep private QR payloads out of command
arguments and output, reuse configured profiles, and list groups first with an
optional groups-only filter.

Profile commands honor MEDRE environment overrides, keep native processes stopped
while reading private QR input, and escape terminal controls in discovered names.
Unknown native exception text remains private.

Show safe profile ownership and configuration validation errors. Refuse existing
profile directories with group or other access on POSIX without changing their
permissions. Accept native invitation web links as well as OPENPGP4FPR payloads.
