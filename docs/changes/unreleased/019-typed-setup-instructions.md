# Declare the provisioning journey as typed setup instructions

When the running core provides the optional `NativeSetupInstructions` seam,
the chatmail plugin declares the README's staged journey as ordered setup
instructions on `AdapterSpec.native_setup_instructions`: one step per
contributed command (`init`, `accounts`, `join`, `chats`) with the verbatim
core-managed invocation, a short purpose, and an evidence note stating what
the completed step proves and what it does not prove. The declaration is
informational only: on such cores `medre config native --transport chatmail`
renders the journey offline with an `informational_only` classification and
writes no file. The form contract stays untouched — Chatmail still declares
no `medre config native` form and gains no `native_setup_fields`, and
provisioning remains operator-owned. On cores without the seam the plugin
constructs the spec exactly as before, and the README remains the source of
the journey.

The journey distinguishes private-journal retention from later SMTP acceptance
reported through delivery feedback, matching the native handoff contract.
