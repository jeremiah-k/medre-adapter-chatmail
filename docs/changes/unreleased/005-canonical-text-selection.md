# Select canonical text through the shared core contract

Use core's canonical text selection before transport-owned prefixing and relation
fallbacks. Styled bodies cannot be shadowed by stale compatibility text; unstyled
messages retain legacy text precedence. Malformed native text is rejected.
Preserve an explicit candidate-core CI qualification path.
