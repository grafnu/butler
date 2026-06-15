YOU ARE AN AUTONOMOUS AGENT THAT CAN NOT ASK QUESTIONS. COMPLETE
YOUR WORK WITHOUT ASKING FOR CLARIFICATION, DIRECTION, SUGGESTIONS,
CORROBORATION, OR ANYTHING ELSE. IF YOU MAKE ASSUMPTIONS, INCLUDE
AN "ASSUMPTION: " TAG IN THE ASSOCIATED CHANGE.

Prefer python unless otherwise specified.
mqtt and mosquitto are available on the system.

If the BUTLER_CONN_SPEC env variable is defined, it should use that as the connectivity specification passed in to all tools.
The tools should not use BUTLER_CONN_SPEC directly, but rather the caller should explicitly add it to the command line.
The specification should conform to the `uufi.md` spec (located within the local `udmi/` directory under `udmi/docs/specs/uufi.md`) as defined. Otherwise, the tests should use `mqtt://<branchname>@localhost/` as
the specification, where `<branchname>` is the current git branch (defaulting to `unknown` if not in a git directory).

The `udmi/` directory must exist as a local subdirectory directly within the workspace (e.g., extracted from an archive or populated by any other means). All tools must verify this filesystem layout on startup and immediately raise a hard error if the local `udmi/` directory is not found (this is the only startup requirement that will cause a hard fail). There are no requirements placed on source control mechanism or git cloning for this subdirectory. *If* the `udmi/` directory
is a git repo, then it should be kept up to date with the currently configured branch (using a `git pull` in that directory).


* For `mqtt` connections, the only valid hostname for testing is `localhost`
  * The `setup` utility should perform a connectivity check to see if the local broker is running. If the broker is not running, the setup utility must invoke the local UDMI tool (specifically `udmi/bin/start_local`) to start the broker automatically.
* For `pubsub` connections, it can be assumed that the necessary authentication and cloud resources will already be setup.
  * The tools should perform a connectivity check but not try to change anything in the cloud.

These top-level files/directories MUST NOT be removed or altered:
* AGENTS.md: Agent-specific instructions and mandates.
* REBUILD.md: System rebuild procedures.
* UPDATE.md: Maintenance and update procedures.
* AUDIT.md: Audit an implementation for spec compliance.
* MERGER.md: Specification merge logic.
* WORKFLOW.md: Operational workflow definitions.
* spec/: Formal system specifications (e.g., butler.md, blobstore.md, update.md). Note that spec/ is considered mutable ONLY when executing the specification merge integration procedure defined in MERGER.md on the gemerger branch.
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
* out/: Output from runtime and integration tests.
* udmi/: Working directory for udmi integration.
