# General instructions for a generic audit.

These instructions only apply to a repo that has a working `butler/`
implementation already in place. If there is no implementation already in
the top-level `butler/` directory, then nothing should happen.

Audit the compliance of the implementation for the specification in `spec/`.

Pay particular attention to the message formats as described in `uufi.md`,
including the exact topics used for communication, and the details
of the JSON schema. Ensure that all messages are sent and received on the
proper topics, and that the JSON is produced in the exact right format.

Update any code that is necessary to be spec compliant, commit and
push the changes.
