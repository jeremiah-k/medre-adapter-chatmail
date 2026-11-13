# Cover the relay prefix surface

Add tests for the Chatmail relay prefix: the default
`[{origin_label}] {sender}: ` template renders onto the outbound body with the
shared fixtures, the empty template yields the bare body, custom templates
resolve both placeholders, and validation accepts the default, custom
templates and disablement while refusing non-text values. Test-only change:
no adapter code, schema, or sample configuration changes.
