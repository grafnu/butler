YOU ARE AN AUTONOMOUS AGENT THAT CAN NOT ASK QUESTIONS. COMPLETE
YOUR WORK WITHOUT ASKING FOR CLARIFICATION, DIRECTION, SUGGESTIONS,
CORROBORATION, OR ANYTHING ELSE. IF YOU MAKE ASSUMPTIONS, INCLUDE
AN "ASSUMPTION: " TAG IN THE ASSOCIATED CHANGE.

Prefer python unless otherwise specified.
mqtt and mosquitto are available on the system.

If the BUTLER_CONN_SPEC env variable is defined, it should use that as the connectivity specification passed in to all tools.
The tools should not use BUTLER_CONN_SPEC directly, but rather the caller should explicitly add it to the command line.
The specification should conform to the `uufi.md` spec as defined. Otherwise, the tests should use `mqtt://<branchname>@localhost/` as
the specification, where `<branchname>` is the current git branch (defaulting to `unknown` if not in a git directory).

* For `mqtt` connections, the only valid hostname for testing is `localhost`
  * Only the `setup` utility should perform a connectivity check and start a local mqtt server if necessary.
* For `pubsub` connections, it can be assumed that the necessary authentication and cloud resources will already be setup.
  * The tools should perform a connectivity check but not try to change anything in the cloud.

These top-level files/directories MUST NOT be removed or altered:
* AGENTS.md: Agent-specific instructions and mandates.
* REBUILD.md: System rebuild procedures.
* UPDATE.md: Maintenance and update procedures.
* AUDIT.md: Audit an implementation for spec compliance.
* MERGER.md: Specification merge logic.
* WORKFLOW.md: Operational workflow definitions.
* spec/: Formal system specifications (e.g., uufi.md, butler.md).
* .wincolor: Terminal configuration.
* .gitignore: Git exclusion patterns.

All functional work and output MUST be contained within:
* bin/: Operational executables.
* butler/: Core Python implementation logic.
* README.md: System overview and documentation.
* impl/: Cross-implementation testing workspace.
* tmp/: Temporary workspace (ephemeral).
* testing/: Test assets and environment.
* venv/: Python virtual environment.
