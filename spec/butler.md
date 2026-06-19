# Butler System Orchestrator

The **Butler** is a declarative, state-based fleet management engine for managed software updates. It coordinates updates across a
fleet of devices by managing a state machine for individual device blob updates using the UUFI interface. UUFI is a message
based interface as part of the UDMI system defined by the path `docs/specs/uufi.md` within the cloned `impl/udmi/` directory (at `impl/udmi/docs/specs/uufi.md` relative to the workspace root).

## 1. Project Structure

The root directory MUST ONLY contain the following files and directories:

### Immutable Metadata and Procedures
- **AGENTS.md**: Agent-specific instructions and mandates.
- **REBUILD.md**: System rebuild procedures.
- **UPDATE.md**: Maintenance and update procedures.
- **AUDIT.md**: Audit an implementation for spec compliance.
- **MERGER.md**: Specification merge logic.
- **WORKFLOW.md**: Operational workflow definitions.
- **.wincolor**: Terminal configuration.
- **.gitignore**: Git exclusion patterns.

### Functional Components and Specifications
- **spec/**: Formal system specifications:
  - `butler.md` (This document)
  - [blobstore.md](blobstore.md): BlobStore provider interface and implementations.
  - [update.md](update.md): Software update message sequence diagram.
- **bin/**: Operational executables and tooling.
- **butler/**: Core Python implementation logic.
- **README.md**: System overview and documentation.

### Operational and Test Artifacts
- **impl/**: Cross-implementation testing workspace (including `test_summary.txt`).
- **tmp/**: Temporary workspace (ephemeral).
- **testing/**: Test assets and environment.
- **udmi_blob_store/**: Static testing Software Catalog and blobs (parallels `udmi_site_model`).
- **venv/**: Python virtual environment.

## 2. Role and Behavior

### 2.1 Orchestrator Behavior
The **Butler** is a stateless, reactive fleet reconciliation engine whose sole scope is to transition devices from their dynamically reported actual version to the expected/desired version specified in the immutable `site_model` (`system.software.<blob_id>`).
- **Device Authority:** The device itself is the sole authoritative source of its current/actual software version and its `lkg_version`. The Butler MUST trust the device's reported state and MUST NOT attempt to track, persistent-store, or validate `lkg_version` history.
- **Stateless Restarts & Network Discovery:** If the Butler process restarts, all in-memory tracking is reset. Sourcing of both expected and actual states occurs exclusively over the UUFI network interface (the Butler has no direct file-level access to the `site_model` on disk):
  1. **Expected Version Discovery:** On startup, the Butler discovers expected/desired versions by publishing a UUFI Model Query (`query/cloud` as defined in `uufi.md`) to `/uufi/c/query/cloud`, where the UUFI gateway (which *does* have site-model access) replies with the expected version configurations.
  2. **Actual Version Discovery:** The Butler simply waits until it receives a dynamic State update from a device to determine its actual version. In local test environments, this actual state report is typically initiated manually or triggered on-demand using standard testing utilities.
- **Handshake Compliance:** Butler MUST NOT initiate its own handshake; it MUST instead respond to handshake state messages from Devices and Verifiers with the appropriate config reply as defined in UUFI. Handshake message structures and sequence steps are governed exclusively by the cloned `impl/udmi/docs/specs/uufi.md` specification; local implementations MUST NOT introduce custom local handshake parameters.
- **State Machine:**
  - `unknown`: Initial tracking state before any device report is received.
  - `quiescent`: Expected/Desired Version == Actual/Current Version.
  - `active`: Expected/Desired Version != Actual/Current Version (Reconciliation required; triggers an update command).
  - `pending`: Update command has been sent to the device, awaiting dynamic state update showing completion or failure. If a transition remains in `pending` for longer than `BUTLER_TIMEOUT` seconds, the orchestrator MUST log a transition timeout warning and automatically retry publishing the update command (up to a maximum of 3 retry attempts, spaced `BUTLER_TIMEOUT` seconds apart). If all 3 retry attempts are exhausted without receiving a status report, the orchestrator MUST log a terminal failure warning, transition the volatile tracking state for that device to `failed` (or `unknown`), and cease sending further commands until a new state report or model update is received.
  The expected/desired version is sourced over the UUFI bus from the Expected Cloud Model (`system.software.<blob_id> = 'version_tag'` as defined in `model_system.json`), where `<blob_id>` is the identifier of the target software. The actual/current version is dynamically reported by the device under the same path in state messages.

- **Triggering:** The orchestrator re-evaluates state and triggers update commands immediately upon receiving device status reports showing version drift (`expected != actual`), unless the device is already in a `pending` transition.
- **No Rollback:** The Butler MUST NOT manage, track, or trigger rollbacks. If an update fails, the Butler simply reports the terminal state. Any rollback or reversion is the domain of the device itself (internally) or an administrator manually updating the expected version in the immutable `site_model`.
- **Efficiency:** State transitions and version reconciliation MUST be processed immediately upon receipt of relevant state messages to minimize end-to-end latency.

### 2.2 Model and Update Management
- **Local Model (Software Catalog Only):** Sourced from `BUTLER_MODEL_FILE` (default: `udmi_blob_store/model.json`), the on-disk Butler database acts exclusively as a **Software Catalog (Package Metadata Database)**. It MUST NOT store any device-specific information, `lkg_version`, or transient update states. It is strictly used to answer catalog queries (such as *"What potential versions for a particular make/model/blob are available?"* or *"What are the metadata parameters/bits for a particular make/model/blob?"*). To ensure any newly registered or updated packages are instantly available and prevent out-of-sync cache errors, the Butler MUST query the physical file on disk dynamically for every metadata and package resolution query.

## 3. Functional Components

### 3.1 Blob Repository
- **Structure:** `{base_dir}/{make}/{model}/{blob_id}/{version}/` (where `{blob_id}` is the software or subsystem identifier).
- **Contents:**
  - `bundle.bin`: The binary blob content.
  - `sha256.txt`: Hex-encoded SHA-256 hash of `bundle.bin`.
- **Integrity:** Every blob requires a SHA256 hash for verification.

### 3.2 Model Repository (Desired State)
- **Outsourced Functionality:** Sourcing and managing the Model Repository (expected configuration and desired state of devices) is completely outsourced to the UDMI environment and handled reactively over the UUFI communication bus. The Butler orchestrator MUST NOT have direct file-system access to the site model, nor does it store any device configuration.
- **UUFI Sourcing:** The expected/desired versions are discovered and updated strictly via UUFI messages (e.g., publishing a UUFI Model Query `query/cloud` and receiving Model Update events over the bus).
- **Global Fleet Scope (Non-Site-Specific):** One instance of Butler (and all other tools) MUST work globally for all sites and devices. There should be NO parameter at all (explicit or implicit) to control or limit which site or registry they process. Butler reactively subscribes to and processes message streams for all site IDs (`site_id`) and device IDs (`device_id`) encountered over the UUFI bus.

### 3.3 Device Conduit (Client-side / DUT)
- **Reporting:** Periodically publish the actual/current version (under the standard `system.software.<blob_id>` path), `status`, and `lkg_version` via state messages.
- **Payload Structure:** State reports MUST include `make` and `model` fields within the target `<blob_id>` nesting to ensure the orchestrator can correctly identify the device type. For consistency across implementations, implementations MUST use the `blobs` wrapper key within the `blobset` state report.
- **Lifecycle:** `quiescent` -> `pending` (download/verify) -> `success` or `failure`.
- **Transitions:** Transitions to `success` or `failure` MUST only occur from the `pending` state. A direct transition from `quiescent` to `success` or `failure` is a protocol violation. System and Verifier components MUST ensure that state reports are processed in the order they were generated to avoid false-positive violations. Additionally, to avoid race conditions during the initial handshaking and command propagation phases, system components MUST NOT interpret pre-update quiescent state reports (sent by the device before it receives the update command) as a termination of the pending transition; the pending tracking state MUST remain active until the device explicitly reports the transition to pending or reaches its final terminal state with the new target version.
- **Robustness:** Devices MUST robustly handle immediate state change requests (back-to-back config updates) and ensure eventual consistency with the latest expected/desired target state.

## 5. Operational Sequences

### 5.1 Update Flow
1. **Initiation:** The expected/desired version is updated in the live Cloud Model via the UDMI `site_trigger` utility (Scope 3) which updates the local model file on disk and publishes a corresponding `model/cloud` Model Update message over the UUFI bus, emulating a database update.
2. **Status Report:** The Device (DUT) publishes its actual/current version and status in its State reports.
3. **Detection:** The Butler detects a version mismatch between the expected version (live Cloud Model) and actual version (device state), and queries the Software Catalog (`BUTLER_MODEL_FILE`) to find the available package metadata matching the device's `{make}/{model}/{blob_id}/{version}`.
4. **Command:** The Butler publishes a `blobset` config payload containing the update package URL and validation parameters over the UUFI bus.
5. **Pending:** The Device reports `pending` state and begins downloading/applying the update.
6. **Completion:** The Device reports `success` or `failure` (along with its updated actual version and `lkg_version`) in its state reports, transitioning the active tracking loop. The Butler does NOT orchestrate rollbacks; rollback or reversion is managed internally by the device itself or by subsequent manual modification of the immutable `site_model` target. **Compliance Note:** The Orchestrator MUST NEVER trigger an update command to revert a device to an older LKG version upon failure or timeout.

## 6. Standard Tooling CLI Interface (bin/)

All tools MUST support the `<conn_spec>` argument (e.g., `mqtt://localhost`). It MUST be supported both as a positional first argument and via an explicit `--conn_spec` flag. On startup, all tools MUST output their connectivity parameters in a consistent format: `Conn spec: scheme={scheme}, host={host}, port={port}, principal={principal}, prefix={prefix}`. This output MUST be directed to `stderr` if the tool is designed to produce machine-readable data on `stdout`.

To ensure interoperability and environmental isolation, tools MUST NOT fail if optional arguments (indicated by `[]`) are omitted, provided a valid default can be determined. When running in a multi-client environment (e.g., parallel testing), implementations MUST strictly adhere to the `Prefix Isolation` requirements defined in UUFI. Specifically, test runners (`smokeit`) MUST incorporate the provided connection prefix into all internally generated topics and child process arguments to prevent cross-trial interference.

These are the ONLY files that should be in the `bin/` directory.

- **butler [conn_spec] [-f]**: Starts the system orchestrator.
- **setup [conn_spec] [--offline] [--stop]**: Ensures the local environment (e.g., MQTT broker) is ready, or cleanly shuts down any running background instances using local PID files if `--stop` is specified. The `--offline` flag allows validating dependencies safely inside hermetic, offline test sandboxes without causing network-related pip warnings or retry latency.
- **verifier [conn_spec]**: Starts the independent verification tool.
- **smokeit [conn_spec]**: Basic integration test.

### 6.1 CLI Compatibility Note
To ensure interoperability, the startup connectivity output MUST use the resolved numeric port (e.g., `1883`) for the `port` field; it MUST NOT be `None` or empty. If a connection string does not specify a path (and thus has no prefix), the `prefix` parameter MUST be output as `None` (e.g., `prefix=None`).

### 6.1 UDMI Tools Dependency & Prohibition of Custom Tooling
Implementations MUST NOT create, bundle, or include custom executable tooling, device clients, or auxiliary scripts (such as custom mock device clients, "mockets", or passive traffic observers) within the workspace.
* **Hard Dependency on UDMI:** UDMI is considered a hard dependency of the Butler orchestrator.
* **No Custom Device Simulation (DUT):** Under no circumstances shall an implementation attempt to create, bundle, or execute its own custom simulated device client (DUT) for any purpose (including local development, debugging, and automated integration/smoke testing). All device simulation must be performed strictly using the standard Java-based UDMI DUT client (`impl/udmi/bin/start_dut` / `pubber`).
* **Standard Simulators & Observers:** For device simulation and traffic observation, implementations MUST use standard UDMI/UUFI tools exclusively (specifically `impl/udmi/bin/start_dut` for starting simulated devices/Pubber, and `impl/udmi/bin/observe_uufi` for passive topic tree traffic monitoring).
* **Automated Smoke Testing (`smokeit`):** The automated integration test runner (`smokeit`) MUST NOT embed, spawn, or execute any custom device simulation logic or programmatic inline mock devices. It MUST use the standard UDMI DUT client for verifying device connectivity, handshakes, and baseline integration.
* **Automatic Audit Verification:** Automated compliance verifiers and audits (e.g., `AUDIT.md`) MUST verify the strict cleanliness of the `bin/` directory and codebase. The presence of any custom simulated device clients or additional executable files beyond the four core tools (`butler`, `setup`, `verifier`, `smokeit`) constitutes an immediate and fatal protocol compliance violation.

### 6.2 CLI Compatibility Note
To ensure interoperability, implementations MUST correctly handle the transition from positional to optional arguments. A common pitfall is allowing an optional `[conn_spec]` to consume the first required positional argument (e.g., `site_id`). Implementations MUST inspect the first positional argument and, if it does not match a valid connection schema (e.g., `mqtt://`), treat it as the first functional argument of the tool.

The startup connectivity output MUST use the resolved numeric port (e.g., `1883`) for the `port` field; it MUST NOT be `None` or empty. If a connection string does not specify a path (and thus has no prefix), the `prefix` parameter MUST be output as `None` (e.g., `prefix=None`).

## 7. Standard Configuration Environment Variables

- **`BUTLER_CONN_SPEC`**: Default connection specification URL.
- **`BUTLER_MODEL_FILE`**: Path to local model JSON (default: `udmi_blob_store/model.json`).
- **`BUTLER_BLOBSTORE_PROVIDER`**: Specifies the pluggable BlobStore implementation to use. Supported values are `local` (Reference Local Disk Storage) or `gcs` (GCP Google Cloud Storage). Default: `local`.
- **`BUTLER_BLOBS_DIR`**: Base directory for local packages when using the `local` provider (default: `udmi_blob_store/packages`).
- **`BUTLER_GCS_BUCKET`**: Target Google Cloud Storage bucket name when using the `gcs` provider (e.g., `my-update-bucket`).
- **`BUTLER_TIMEOUT`**: Timeout for `pending` state transitions (default: `60`).
- **`GOOGLE_APPLICATION_CREDENTIALS`**: Optional path to GCP Service Account JSON key file used by the `gcs` provider to authenticate and sign URLs.
- **`UDMIS_REFLECTOR_CONFIG`**: Path or configuration parameters for the UDMIS reflector, used to dynamically configure the reflector and bypass hardcoded cloud model dependencies to enhance portability.

## 8. Robustness

- **Idempotency:** All components MUST be idempotent.
- **Deduplication:** Track message `transaction_id` (or `transactionId` in envelope) for at least 5 minutes. Implementations MUST support tracking transaction IDs as arbitrary string values (which can include 8-digit hex strings, UUIDs, or structured session strings like `UUFI:sess123:001`). This deduplication filter MUST be applied to incoming Model Update and Command/Config messages to prevent duplicate transition actions, but MUST NOT discard or skip processing of incoming Device State reports (which are authoritative and must always be processed immediately).
- **Partial Merge:** `cloud` model `UPDATE` operations MUST be partial merges at the device subsystem level; existing fields not in the payload MUST NOT be modified.
- **Envelope Key Standardization:** To prevent integration-time validator warnings (such as `"redundant subType in envelope"` and `"redundant deviceRegistryId in envelope"`), implementations MUST adhere to strict envelope schema rules:
  - **`subType` Elimination:** The `subType` attribute MUST NOT be included in the envelope of device state (`state`) or command/config (`config`) messages where the topic structure itself or the context already determines the subtype.
  - **`deviceRegistryId` Minimization:** The `deviceRegistryId` (representing the `site_id` in local UUFI contexts) MUST NOT be populated in local device-scoped message envelopes where the registry identity is already fully established or implied by the endpoint topic path, ensuring standard compliant validation.

## 9. Verification and Observability

### 9.1 Verifier (Active Observer)
- **Handshake:** MUST complete UUFI handshake.
- **Monitoring:** Track state transitions in the `blobset` subfolder.
- **Reporting:** Publish validation results to `[/{prefix}]/uufi/r/{site_id}/d/{device_id}/c/events/validation`. For events related to a specific device, `{site_id}` and `{device_id}` MUST match the device. For self-reporting (e.g., handshake status), `{device_id}` MUST be the verifier's identity (e.g., `verifier`) and `{site_id}` MUST be `unknown` unless a specific site/registry has been discovered.
- **Processing:** Verifier components MUST ensure that messages from the same device/blob_id are processed sequentially (e.g., via a message queue) to maintain accurate state tracking and avoid false-positive transition violations.

### 9.2 Compliance Logging
For automated interoperability testing and verification, implementations MUST adhere to the following log formats for critical lifecycle events:
- State Transitions (Verifier): `VERIFIER [INFO]: State transition for {site_id}/{device_id}/{blob_id}: {old_state} -> {new_state}`. To ensure strict consistency and automated parser reliability, verifiers MUST always output this format containing the full `{site_id}/{device_id}/` segment. The initial state before any report is received MUST be considered `unknown`. To ensure log clarity, verifiers MUST NOT log a transition if the `{new_state}` is identical to the `{old_state}`. This prohibition applies to both the standard output logging and the publication of validation events (Section 9.3) on the UUFI bus.
- Handshake Events (Verifier): `VERIFIER [INFO]: Handshake {started|completed} for {principal}`.
- **Validation Errors (Verifier):** `VERIFIER [ERROR]: VALIDATION ERROR: {message}`.
- **Terminal State (Orchestrator):** `[butler] Device {site_id}/{device_id}/{blob_id} terminal state {status} with version {version}`. Terminal states MUST include `success`, `failure`, and `quiescent` (even if the version is `"0.0.0"`). This log MUST be generated whenever a device enters or reports one of these states.
Consistent log prefixes and formats are essential for multi-implementation integration testing. These messages MUST be printed exactly as specified, without additional prefixes (e.g., timestamps or thread IDs) that might interfere with automated log analysis.

### 9.3 Validation Event Schema
- **Topic:** `[/{prefix}]/uufi/r/{site_id}/d/{device_id}/c/events/validation`
- **Payload:** The `validation` object within the `payload` MUST include:
  - `message`: A human-readable description of the validation event.
  - `level`: One of `INFO`, `WARN`, or `ERROR`.
  - `device_id`: (Optional) The ID of the device being validated.
  - `blob_id`: (Optional) The ID of the software/subsystem being validated (matches the `system.software.<blob_id>` key).
  - `status`: (Optional) The current state (e.g., `pending`, `success`).
  - `result`: (Optional) One of `pass` or `fail` (case-insensitive).

## 10. Development and Testing Workflow (Scope 4)

The fourth tier of the system verification pipeline builds directly on top of the generic UUFI development environment (reusing Scope 1: Infrastructure and Scope 2: Pubber DUT from `uufi.md` Section 9). It replaces the low-level UUFI test client (Scope 3) with the **Butler Orchestrator**, executing a complete state-based firmware update and rollback orchestration cycle over the active broker.

To ensure that multiple disparate implementations can be run side-by-side using the same shared UDMI installation without conflicts, created systems MUST be run independently in their respective local directories. This requires:
1. **Model Cloning:** Copy the pre-existing test site model from the cloned `impl/udmi` sites directory into your local workspace testing directory.
2. **Port Selection & Handshake Verification:** Choose and use a unique, branch-specific port in the range `45000-48000` (inclusive of `45000`, exclusive of `48000`, i.e., `45000-47999`) for running the local MQTT broker to prevent port conflicts with other side-by-side runs and avoid potential overlaps with standard system daemons. Implementations MUST calculate this port systematically using the SHA256 cryptographic hash of the active git branch name to align port-selection behavior across all implementation branches:
   - **Branch Name Extraction:** Determine the active git branch name. If the environment is not a git repository or the branch name cannot be resolved, default to the string `"unknown"`.
   - **Hash Computation:** Compute the 32-byte (256-bit) SHA256 hash of the resolved branch name string (encoded in UTF-8).
   - **Integer Conversion:** Interpret the entire 32-byte SHA256 hash digest (big-endian) as a single large integer (or interpret its 64-character hex-encoded string representation as a base-16 integer).
   - **Range Mapping:** Map the integer to the 3,000-port range using modulo, and apply the offset `45000`:
     `port = 45000 + (hash_integer % 3000)`
   - **Concrete Hashing Examples:**
     - **Input branch name `"main"`:**
       - UTF-8 bytes: `b"main"`
       - SHA256 hex digest: `0d6e4079e36703ebd37c00722f5891d28b0e2811dc114b129215123adcce3605`
       - Base-16 Integer value modulo 3000: `2093`
       - Resolved numeric port: `47093` (i.e., `45000 + 2093`)
     - **Input branch name `"unknown"`:**
       - UTF-8 bytes: `b"unknown"`
       - SHA256 hex digest: `b23a6a8439c0dde5515893e7c90c1e3233b8616e634470f20dc4928bcf3609bc`
       - Base-16 Integer value modulo 3000: `988`
       - Resolved numeric port: `45988` (i.e., `45000 + 988`)
   - **Dynamic Port Handshake Verification:** During broker startup, the setup script or test runner MUST perform a socket-scanning check to ensure the calculated port is unoccupied by another daemon process on the host. If a port collision is detected, the utility MUST dynamically negotiate an alternative free port (e.g., by scanning sequentially upward from the initial port or utilizing OS port allocation) to guarantee a clean connection handshake.
3. **Working Directory Execution:** Execute all UDMI commands using the executables in the cloned `impl/udmi` folder.

### 10.1. Local Environment Preparation

#### 10.1.1. Automatic Environment & Pip Requirement Validation
Before copying the site model or configuring resources, the setup pipeline MUST validate the Python runtime environment. If a virtual environment (`venv`) is not currently active, it should be activated or created. Additionally, we must verify that all packages specified in the requirements file (`butler/requirements.txt`) are fully satisfied; if they are not, they must be automatically installed using `pip`.
The validation sequence MUST dynamically establish the Python virtual environment and automatically execute `pip install` to satisfy dependency requirements prior to executing setup utilities.
If the `--offline` flag is provided to the setup utility, it MUST NOT attempt to make remote network calls for package verification or installation. Instead, it must either perform package verification using local caches or ignore missing dependencies to guarantee safe, warning-free offline execution inside hermetic test sandboxes without experiencing download retry latencies.

#### 10.1.2. Isolated Site Model Setup
Establish an isolated copy of the pre-existing test site model by copying the model from the cloned UDMI sites directory (`impl/udmi/sites/udmi_site_model`) into the local workspace testing directory (`testing/udmi_site_model`).

#### 10.1.3. Running Setup and Starting/Stopping the Broker
Next, run the Butler setup utility to prepare the environment (initializing local workspace directories, local model files, and other Butler-specific resources). The utility MUST first verify that the cloned `impl/udmi` directory exists directly, immediately raising a hard fail if it is missing. Execute the setup utility pointing to the dynamically resolved branch-specific port. If the local broker is not already running on that port, the setup utility MUST automatically invoke the cloned UDMI start utility to start it on that unique port.

*   **Graceful Reflector Cleanup/Shutdown (`--stop` flag):**
    If the `--stop` flag is passed (e.g., `bin/setup --stop` or `setup [conn_spec] --stop`), the setup utility MUST NOT perform any environment initialization, python environment validation, or broker startup. Instead, it MUST execute a clean, hermetic teardown of the locally running background services (`etcd`, `mosquitto`/broker, etc.) utilizing stored PID files (`out/etcd.pid`, `out/mosquitto.pid`, or similar). 
    Specifically, the `--stop` execution sequence MUST:
    1. Verify the existence of the specific local `.pid` files associated with the background services.
    2. Read the recorded process IDs (PIDs) from these files.
    3. Send `SIGTERM` (signal 15) directly and precisely to those individual PIDs to initiate a graceful shutdown.
    4. Wait for a graceful grace period of up to 5 seconds for the processes to release ports and exit.
    5. If a process remains active after the grace period, send `SIGKILL` (signal 9) to force termination.
    6. Delete the `.pid` files from disk, leaving the local workspace in a clean state.
    This shutdown MUST NOT employ any sweeping `pkill` or `killall` commands, ensuring absolute safety for concurrent, side-by-side local test runs on different ports.

*   **Automatic Port Status Pre-Check:**
    Prior to launching any local brokers, the setup utility MUST perform a quick, dynamic port-scanning check on the dynamically resolved branch-specific port and standard etcd/MQTT ports. If the target port is already occupied, the setup utility MUST detect and list the active process info and PID (if accessible) to standard error (`stderr`) before attempting any execution. This makes it easier to debug when a rogue or manually started broker has occupied ports outside the test runner's orchestration.

### 10.2. Starting the Butler Orchestrator
Launch the core Butler orchestrator. It MUST connect to the running MQTT broker on the dynamically resolved branch-specific port and act as the authoritative Cloud Model Server on the UUFI bus.

### 10.3. Starting the Independent Verifier
Run the verifier tool in a separate terminal, pointing it to connect to the dynamically resolved branch-specific port on localhost to perform active verification.

### 10.4. Starting the Device Under Test (Pubber DUT)
Launch the simulated on-premise device in a separate terminal using the standard Java-based UDMI DUT client (`pubber`). Point the client to the isolated site model copy (`testing/udmi_site_model`) and the dynamically resolved branch-specific port.
*Note:* The Butler orchestrator coordinates managed updates. While **Pubber** connects and handshakes successfully, it may fail to fully execute the specific firmware state transitions (`quiescent` -> `pending` -> `success`/`failure`) that a custom UDMI client might report. Let the tests fail on these steps if Pubber lacks full update state-machine capabilities; this is expected behavior to verify platform readiness.

### 10.5. Triggering a Managed Update (Functional Verification)
Initiate a managed software update by using UDMI's site trigger utility (located in the cloned `impl/udmi` folder) to mutate the physical site model file on disk and publish the dynamic update event over the UUFI bus on the dynamically resolved branch-specific port.

### 10.6. Running Automated Smoke Tests
To execute a fully automated, non-interactive integration run of Scope 4 (verifying the entire setup, registration, update, rollback, and verification lifecycle), execute the `smokeit` test utility pointing to the dynamically resolved branch-specific port.

### 10.7. Automated Smoke Test Specifications
Any automated integration test harness (such as `bin/smokeit`) MUST adhere to the following strict operational requirements to ensure reliable, isolated side-by-side executions:
1. **MQTT Event Loop Activation:** Every MQTT client instance instantiated by the test harness (including log-reading watchers and cloud-mutation triggers) MUST run a background network event loop (via `loop_start()` or equivalent) to actively read and process incoming broker packets (such as QoS=1 `PUBACK` confirmations). Clients MUST NOT call `wait_for_publish()` without an active event loop running, to prevent execution hangs.
2. **Working Directory and Log Path Resolution:** The test harness MUST execute all external utilities (including `start_dut` or `pubber`) with the working directory explicitly set to the isolated workspace root. Because external utilities write their log outputs (specifically `pubber.log`) relative to their execution working directory, the test harness MUST resolve and monitor the log file path relative to its own local execution directory (e.g., `out/pubber.log` under the workspace root), rather than reading from any global or shared directories (such as the cloned `impl/udmi` folder), ensuring complete isolation of side-by-side test runs.
3. **UDMIS Startup Synchronization & Parallel Daemon Bootstrapping:** The test harness MUST implement a startup synchronization delay (e.g., waiting for `pod_ready.txt` or a standard timeout of at least 15 seconds) after starting the local UDMIS service pod and BEFORE launching the simulated device (DUT), ensuring all dynamic security roles and MQTT subscriptions are active before the client-initiated handshake begins. To optimize execution latency and minimize overall integration test times, the test harness (such as `bin/smokeit`) MUST spin up the Butler Orchestrator and Verifier concurrently in parallel threads or background processes while this synchronization delay is running, rather than waiting for the synchronization period to completely finish before starting those daemons.
4. **Process Group Isolation, Clean Sequestering, and Safe Cleanup:** To prevent lingering orphan or daemon processes (such as background `java`, `etcd`, or `mosquitto` processes) from remaining active on the host after a test run concludes or fails, the test harness MUST launch all background services (including Butler, Verifier, UDMIS, and the simulated DUT) in distinct, isolated process groups (e.g., using `preexec_fn=os.setsid` or equivalent process group detachment).
   - **Safe Process Termination and Agent Protection:**
     - To ensure that process termination WILL NOT UNDER ANY CIRCUMSTANCES impact an agent running the system (such as the encapsulating agentic `gemini` process), any process termination, signal propagation, or process group signaling (like `killpg` or `kill -- -PGID`) MUST be targeted exclusively and precisely to the specific isolated child process groups created for the background services.
     - Under no circumstances should a test harness, tool, runner script, or skill signal or terminate its own process group (such as with `killpg(0, ...)` or equivalent, `kill 0`, `kill -$$`, `kill -- -$$`, `kill -PGID`, or equivalent), regardless of whether it believes the process group is shared. To prevent accidental termination of the encapsulating agentic environment or CLI shell, all termination calls in exit traps and cleanup routines MUST target ONLY the specific, individually-stored process IDs (PIDs) or isolated child process groups of the background services. It is strictly forbidden to use sweeping group-based kill commands (like `kill 0` or signaling the script's own PID/PGID) in any exit traps, runner scripts, or teardown routines.
     - All signal hooking, traps, and teardown procedures must be cleanly sequestered so that termination of background processes does not terminate or affect the running agentic environment or CLI shell. When sending signals, the test harness must only signal the stored PGID of the spawned child process groups.
     - If a test harness or skill needs to run in a way that allows sweeping teardowns of its own process group, it MUST be launched in its own separate process group first (e.g., via `os.setsid()` or separate process spawning) to guarantee that any termination signals sent to its process group do not escape to the parent agent.
   - **Signal Hooking and Exit Traps:** Runner scripts and test harnesses (specifically `bin/smokeit`) MUST register signal handlers/traps for standard termination signals (specifically `SIGINT` and `SIGTERM`) and normal exits to guarantee the immediate and clean teardown of all background processes (specifically `etcd`, `mosquitto`, and UDMIS/`java`) on unexpected user interrupts, completely preventing leaked ports and zombie processes during rapid local development:
     - *Python Harnesses:* MUST catch `KeyboardInterrupt` and register handlers with Python's signal module to invoke process group termination on all active child process groups.
     - *Bash Runner Scripts:* MUST register a trap on standard exit and termination signals to automatically terminate only the specific spawned background jobs. This MUST be done by explicitly killing the individual background job process IDs (e.g., using `kill $(jobs -p)` or specific list of recorded PIDs) and MUST NOT use sweeping group-based kills like `kill 0`, `kill -$$`, or signaling the current shell's process group.
   - This automatic cleanup MUST occur reliably on both success and failure, preventing port leakage, zombie processes, and resource conflicts between rapid sequential test cycles.
   - **Hermetic Local Daemon Teardown Sequence (etcd and mosquitto):**
     - To ensure that side-by-side local integration runs do not disrupt other development environments, running docker containers, or system-wide services, the test harness MUST NOT use generic `pkill` or `killall` commands (such as `pkill etcd` or `pkill mosquitto`) to stop background services.
     - Instead, the test setup utility and test harness MUST write the specific process IDs (PIDs) of the locally spawned `etcd` and `mosquitto` daemons to dedicated, distinct local PID files within the workspace's output or temp directory (e.g., `out/etcd.pid` and `out/mosquitto.pid`).
     - During the teardown or cleanup phase, the script MUST:
       1. Check for the existence of the specific `.pid` files.
       2. Read the recorded PID from each file.
       3. Send `SIGTERM` (signal 15) directly to that specific PID.
       4. Wait for a graceful grace period (e.g., 2–5 seconds) for the process to exit and release its socket/port.
       5. If the process is still running after the grace period, send `SIGKILL` (signal 9) to force termination.
       6. Delete the associated `.pid` file from disk upon successful termination.
5. **Dynamic Reflector Mapping:** The UDMIS reflector component MUST utilize dynamic configurations based on local environment variables to bypass hardcoded cloud model dependencies, enhancing portability across different testing environments.

## 11. Principal Suffix Standardization
To ensure consistency across multiple implementations and avoid custom differentiator mismatches during handshake verification and log analysis, all system entities MUST adhere to a standardized principal naming schema. 

### 11.1. Principal Structure
Every system component, tool, or utility MUST resolve and report its principal identity using the dot-separated format:
`{implementation_id}.{entity_suffix}`

Where:
*   `{implementation_id}` represents the unique identifier of the specific implementation run or branch (e.g., `impl_A`, `butler_py`).
*   `{entity_suffix}` is a standardized suffix corresponding precisely to the functional role of the executing entity.

### 11.2. Standard Suffix Mapping
Implementations MUST map entity roles to the following standardized suffixes:

| System Entity / Tool | Standardized Suffix | Example Principal (for `impl_A`) |
| :--- | :--- | :--- |
| Environment Setup / Bootstrapping Utility | `.setup` | `impl_A.setup` |
| Butler Orchestrator | `.butler` | `impl_A.butler` |
| Independent Verifier Tool | `.verifier` | `impl_A.verifier` |
| Simulated Device Under Test (Pubber DUT) | `.device` | `impl_A.device` |
| Automated Test Harness (`smokeit`) | `.smokeit` | `impl_A.smokeit` |

### 11.3. Enforcement and Connection Verification
During the handshake verification phase (Scope 4), all validation tools, logs, and diagnostic events MUST parse and verify these exact principal strings. Any custom or non-standard differentiator suffixes (e.g., `_setup`, `.orchestrator_daemon`, or `.test_runner`) are considered protocol violations and MUST be treated as handshake/verification failures.

## 12. Protocol Payload Formatting and Envelope Attributes

To ensure complete interoperability and spec compliance across multiple side-by-side implementations, all message payloads and network envelopes MUST adhere strictly to the following unambiguous formatting standards. Implementations and testing frameworks MUST NOT deviate from these standards, and any components employing non-compliant formats (such as nested wrappers or custom configuration attributes) MUST be treated as protocol violations and fail verification.

### 12.1. Handshake Request and Reply Payload Formatting
Handshake protocol requests (Step 1) and replies (Step 2) published over the UUFI bus MUST utilize the standard flattened format where the `"setup"` and `"reply"` payload blocks reside directly at the payload root. Wrapping or nesting these blocks inside a `"udmi"` root sub-object is strictly prohibited and MUST be rejected as non-compliant.

### 12.2. Subsystem State and Catalog Model Alignment
Simulated devices and DUTs MUST report their actual software and firmware states under the `"system"` subsystem ID (rather than `"main"` or other custom names) to ensure alignment with cloud model catalog updates, which configure desired software versions under the standard `"system"` schema. Furthermore, state reports MUST wrap the subsystem inside a `"blobs"` key inside the `"blobset"` state payload (e.g., `blobset: { "blobs": { "system": { ... } } }`) to align with the standard UDMI schemas.

### 12.3. Config Command Target Version Attribute
Any `"blobset"` update config command published by the orchestrator MUST include the target `"version"` string attribute inside the specific blob's dictionary (e.g. `blobset.blobs.<subsystem>.version = "{version}"`). This indicates the target version of the update package, enabling the client or DUT to parse it and successfully complete the update sequence.

### 12.4. Envelope Nonce Attribute
To support robust message deduplication and replay protection, clients and devices publishing state, event, or model messages over the UUFI bus SHOULD include a `"nonce"` field in the root of the message's envelope containing a secure, pseudorandomly generated hexadecimal string (at least 32 characters, e.g. 16 bytes). Compliant orchestrators and verifiers MUST gracefully accept, parse, and process envelopes containing the `"nonce"` attribute.

### 12.5. Cloud Model Update Payload Structure
Cloud model updates published over the UUFI bus (e.g., on `/uufi/c/config/cloud` or model update channels) MUST utilize the standard flattened format where the `"registries"` key resides directly at the payload root (following the schema defined in `uufi.md` Section 5.1). Wrapping or nesting the update payload inside a `"cloud"` root sub-object is strictly prohibited and MUST be rejected as non-compliant.

### 12.6. Single Method for Expected Version Configuration
The expected/desired version of a device's software subsystem MUST be configured in exactly one way: under the standard software dictionary structure within the device's system configuration (e.g., `system.software.<subsystem> = "{version}"`, where `<subsystem>` defaults to `"system"`). Any alternative or custom configuration properties, such as `"target_version"` (e.g., `system.target_version = "{version}"`), are strictly prohibited and MUST NOT be accepted by the orchestrator or processed as valid expected versions.

### 12.7. Topic Suffix Standard Formatting
To maintain strict compliance with the UUFI topic routing specification, all UUFI topic paths MUST include both a subtype and a subfolder segment, formatted strictly as `/c/{subtype}/{subfolder}`. Omitting the subfolder segment or formatting topic suffixes as `/c/{subtype}` is non-compliant. For standard registry-less handshakes, the subfolder segment MUST be explicitly set to `"udmi"` (e.g., `/uufi/c/state/udmi` and `/uufi/c/config/udmi`). Topic building and routing components MUST NOT generate topic paths lacking either segment.
