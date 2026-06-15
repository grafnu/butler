YOU ARE AN AUTONOMOUS AGENT THAT CAN NOT ASK QUESTIONS. COMPLETE
YOUR WORK WITHOUT ASKING FOR CLARIFICATION, DIRECTION, SUGGESTIONS,
CORROBORATION, OR ANYTHING ELSE. IF YOU MAKE ASSUMPTIONS, INCLUDE
AN "ASSUMPTION: " TAG IN THE ASSOCIATED CHANGE.

Prefer python unless otherwise specified.
mqtt and mosquitto are available on the system.

If the BUTLER_CONN_SPEC env variable is defined, it should use that as the connectivity specification passed in to all tools.
The tools should not use BUTLER_CONN_SPEC directly, but rather the caller should explicitly add it to the command line.
The specification should conform to the `uufi.md` spec (located within the peer `udmi/` directory under `../udmi/docs/specs/uufi.md`) as defined. Otherwise, the tests should use `mqtt://<branchname>@localhost/` as
the specification, where `<branchname>` is the current git branch (defaulting to `unknown` if not in a git directory).

<!-- ASSUMPTION: Sibling/Peer udmi directory (at ../udmi) is a shared, read-only resource. User direct command overrides the file edit restriction on AGENTS.md for this setup update. -->
The `udmi` (directory or link) must exist as a peer directory directly sibling to the repository directory (e.g., at `../udmi` relative to the workspace root). All tools must verify this filesystem layout on startup and immediately raise a hard error if the sibling/peer `udmi` directory or link is not found (this is the only startup requirement that will cause a hard fail).

**Shared and Read-Only Resource Constraints:**
The peer `udmi` directory/link is a shared resource and MUST be treated as read-only. It is only suitable for running standard immutable executables or referencing static specs/metadata. Modifying files directly within `../udmi/` (such as cloning site models there) is strictly prohibited to prevent race conditions during multi-implementation runs.

**Expected Peer Directory Structure and Utilities:**
- `../udmi/bin/setup_base`: Sudo/system package setup script (optional if system dependencies like mosquitto, openjdk, and expect are already satisfied).
- `../udmi/bin/start_local`: Tool used to automatically spin up a local broker if not already running on the testing port.
- `../udmi/bin/clone_model`: Tool used to clone standard site models (for reference).
- `../udmi/bin/start_dut`: Tool used to launch the Pubber Device Under Test.
- `../udmi/bin/site_trigger`: Tool used to simulate expected version model updates over the UUFI bus.
- `../udmi/docs/specs/uufi.md`: Formal communication bus specification.

There are no requirements placed on source control mechanism or git cloning for this peer directory. *If* the peer `udmi` directory is a git repo, then it should be kept up to date with the currently configured branch (using a `git pull` in that directory).

**Relative Path Resolution Rule:**
All components MUST resolve relative `file://` paths defined in the Software Catalog (`model.json`) relative to the project workspace root directory, regardless of which subdirectory they are executed from. This ensures path consistency across testing configurations.


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
