# Butler System Orchestrator

**Butler** is a declarative, state-based fleet management engine for managed software updates. It coordinates updates across a
fleet of devices by managing a state machine for individual device blob updates using the UUFI interface. UUFI is a message-based interface as part of the UDMI system defined by the authoritative communication bus specification.

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
- **out/**: Output from runtime and integration tests, and log files.
- **udmi/**: Working directory for local udmi integration.

## 2. Role and Behavior

### 2.1 Orchestrator Behavior
The **Butler** is a stateless, reactive fleet reconciliation engine whose sole scope is to transition devices from their dynamically reported actual version to the expected/desired version specified in the immutable `site_model` (`system.software.<blob_id>`).
- **Device Authority:** The device itself is the sole authoritative source of its current/actual software version and its `lkg_version`. The Butler MUST trust the device's reported state and MUST NOT attempt to track, persistent-store, or validate `lkg_version` history.
- **Stateless Restarts & Network Discovery:** If the Butler process restarts, all in-memory tracking is reset. Sourcing of both expected and actual states occurs exclusively over the UUFI network interface (the Butler has no direct file-level access to the `site_model` on disk):
  1. **Expected Version Discovery:** On startup, the Butler discovers expected/desired versions by publishing a UUFI Model Query (`query/cloud` as defined in the authoritative communication bus specification) to `/uufi/c/query/cloud`, where the UUFI gateway (which *does* have site-model access) replies with the expected version configurations.
  2. **Actual Version Discovery:** The Butler simply waits until it receives a dynamic State update from a device to determine its actual version. In local test environments, this actual state report is typically initiated manually or triggered on-demand using standard testing utilities.
- **Handshake Compliance:** Butler MUST NOT initiate its own handshake; it MUST instead respond to handshake state messages from Devices and Verifiers with the appropriate config reply as defined in UUFI. Handshake message structures and sequence steps are governed exclusively by the authoritative communication bus specification; local implementations MUST NOT introduce custom local handshake parameters.
- **State Machine:**
  - `unknown`: Initial tracking state before any device report is received.
  - `quiescent`: Expected/Desired Version == Actual/Current Version.
  - `active`: Expected/Desired Version != Actual/Current Version (Reconciliation required; triggers an update command).
  - `pending`: Update command has been sent to the device, awaiting dynamic state update showing completion or failure. If a transition remains in `pending` for longer than `BUTLER_TIMEOUT` seconds, the orchestrator MUST log a transition timeout warning and automatically retry publishing the update command (up to a maximum of 3 retry attempts, spaced `BUTLER_TIMEOUT` seconds apart). If all 3 retry attempts are exhausted without receiving a status report, the orchestrator MUST log a terminal failure warning, transition the volatile tracking state for that device to `failed` (or `unknown`), and cease sending further commands until a new state report or model update is received. To ensure robust handling of slow or large updates, a state message from the device that includes a specific, measurable blob progress update (e.g. download percentage or block count) MUST reset the `BUTLER_TIMEOUT` timer. A generic or static state message with no indication of active progress is NOT sufficient to reset the timer.
  The expected/desired version is configured inside the Expected Cloud Model (natively defined by UDMI schemas), where `<blob_id>` is the identifier of the target software. The actual/current version is dynamically reported by the device within its standard State reports. Rather than defining custom payload structures, Butler is concerned strictly with comparing these values at the authoritative paths defined by the UUFI and UDMI specifications (such as expected versions under `system.software` and actual versions under `blobset.blobs`) to drive state transitions.

- **Triggering:** The orchestrator re-evaluates state and triggers update commands immediately upon receiving device status reports showing version drift (`expected != actual`), unless the device is already in a `pending` transition.
- **No Rollback:** The Butler MUST NOT manage, track, or trigger rollbacks. If an update fails, the Butler simply reports the terminal state. Any rollback or reversion is the domain of the device itself (internally) or an administrator manually updating the expected version in the immutable `site_model`.
- **Efficiency:** State transitions and version reconciliation MUST be processed immediately upon receipt of relevant state messages to minimize end-to-end latency.

### 2.2 Model and Update Management
- **Local Model (Software Catalog Only):** Sourced from `BUTLER_MODEL_FILE` (default: `udmi_blob_store/model.json`), the on-disk Butler database acts exclusively as a **Software Catalog (Package Metadata Database)**. It MUST NOT store any device-specific information, `lkg_version`, or transient update states. It is strictly used to answer catalog queries (such as *"What potential versions for a particular make/model/blob are available?"* or *"What are the metadata parameters/bits for a particular make/model/blob?"*). To ensure any newly registered or updated packages are instantly available and prevent out-of-sync cache errors, the Butler MUST query the physical file on disk dynamically for every metadata and package resolution query.

## 3. Functional Components

### 3.1 Blob Repository
- **Structure:** `{base_dir}/{make}/{model}/{blob_id}/{version}/` (where `{blob_id}` is the software or subsystem identifier).
- **Contents:**
  - `bundle.bin` or `bundle.txt`: The raw package payload.
- **Integrity:** Every blob requires a SHA-256 hash for verification. To prevent configuration skew and redundancy, separate static hash files (such as `sha256.txt`) are strictly PROHIBITED. The orchestrator and BlobStore provider MUST dynamically calculate the hex-encoded SHA-256 hash of the payload file (`bundle.bin` or `bundle.txt`) at runtime.

### 3.2 Model Repository (Desired State)
- **Outsourced Functionality:** Sourcing and managing the Model Repository (expected configuration and desired state of devices) is completely outsourced to the UDMI environment and handled reactively over the UUFI communication bus. The Butler orchestrator MUST NOT have direct file-system access to the site model, nor does it store any device configuration.
- **UUFI Sourcing:** The expected/desired versions are discovered and updated strictly via UUFI messages (e.g., publishing a UUFI Model Query `query/cloud` and receiving Model Update events over the bus).
- **Global Fleet Scope (Non-Site-Specific):** One instance of Butler (and all other tools) MUST work globally for all sites and devices. There should be NO parameter at all (explicit or implicit) to control or limit which site or registry they process. Butler reactively subscribes to and processes message streams for all site IDs (`site_id`) and device IDs (`device_id`) encountered over the UUFI bus.

### 3.3 Device Conduit (Client-side / DUT)
- **Reporting:** Periodically publish the actual/current version, `status`, and `lkg_version` via state messages. Butler is concerned strictly with comparing and processing these reported values as required by the update state machine.
- **Payload Structure:** The structural arrangement, schema paths, and nesting of fields (such as actual versions reported under `blobset.blobs.<subsystem_id>.version` and device identity parameters `make` and `model` under the target subsystem nesting) are governed entirely by the authoritative UDMI state schemas. Local Butler components rely on extracting these values from their standard, schema-defined locations rather than introducing custom local wrappers.
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

All tools MUST support the `<conn_spec>` argument (e.g., `mqtts://localhost`). It MUST be supported both as a positional first argument and via an explicit `--conn_spec` flag. On startup, all tools MUST output their connectivity parameters in a consistent format: `Conn spec: scheme={scheme}, host={host}, port={port}, principal={principal}, prefix={prefix}`. This output MUST be directed to `stderr` if the tool is designed to produce machine-readable data on `stdout`.
If no `<conn_spec>` argument is supplied (via flag, environment variable, or positional argument), all tools MUST default to a secure connection specification format using `mqtts://<branchname>@localhost/` (where `<branchname>` is the current git branch name, defaulting to `unknown` if not in a git repository directory). Under no circumstances shall tools default to an unencrypted `mqtt://` connection schema. This ensures that local developer testing utilizes the same secure, certificate-based cryptographic scheme as the cross-implementation matrix runs.

To ensure interoperability and environmental isolation, tools MUST NOT fail if optional arguments (indicated by `[]`) are omitted, provided a valid default can be determined. When running in a multi-client environment (e.g., parallel testing), implementations MUST strictly adhere to the `Prefix Isolation` requirements defined in UUFI. Specifically, test runners (`smokeit`) MUST incorporate the provided connection prefix into all internally generated topics and child process arguments to prevent cross-trial interference.

These are the ONLY files that should be in the `bin/` directory.

- **butler [conn_spec] [-f]**: Starts the system orchestrator.
- **setup [conn_spec] [--offline] [--stop]**: Ensures the local environment (e.g., MQTT broker) is ready, or cleanly shuts down any running background instances if `--stop` is specified. The `--offline` flag allows validating dependencies safely inside hermetic, offline test sandboxes without causing network-related pip warnings or retry latency.
- **verifier [conn_spec]**: Starts the independent verification tool.
- **smokeit [conn_spec]**: Basic integration test.

### 6.1 CLI Compatibility Note
To ensure interoperability, the startup connectivity output MUST use the resolved numeric port (e.g., `1883`) for the `port` field; it MUST NOT be `None` or empty. If a connection string does not specify a path (and thus has no prefix), the `prefix` parameter MUST be output as `None` (e.g., `prefix=None`).

### 6.1 UDMI Tools Dependency & Prohibition of Custom Tooling, Language Runtime Mocks, and Temporary Executables
Implementations MUST NOT create, bundle, or include custom executable tooling, device clients, auxiliary scripts, mock language runtimes, or temporary executables within the workspace.
* **Prohibition of Language Runtime Mocks and Temporary Executables:** Under no circumstances should an implementation use custom language runtime mock wrappers, binary simulation wrappers, or temporary/mock executables (such as mock `java`, `python`, `node`, `ps`, or other executable mocks) to simulate execution, bypass system checks, or spoof service-readiness signals (e.g., touching `pod_ready.txt` without actually booting the runtime daemon). Every workspace environment must run a real, complete, and un-mocked installation of the required language runtimes and compilers (e.g., real Java, real Python, real Node.js).
* **Real Implementations Only:** The testing and execution environments must not be simulated or designated as "mock sandboxes" or "Python-only testing sandboxes" to bypass real runtime requirements; all system components, dependencies, and environments MUST be real, fully-featured, and completely compliant with the specification (no mocks).
* **Hard Dependency on UDMI:** UDMI is considered a hard dependency of the Butler orchestrator.
* **No Custom Device Simulation (DUT):** Under no circumstances shall an implementation attempt to create, bundle, or execute its own custom simulated device client (DUT) for any purpose (including local development, debugging, and automated integration/smoke testing). All device simulation must be performed strictly using the standard, compliant simulated DUT client.
* **Standard Simulators & Observers:** For device simulation and traffic observation, implementations MUST use standard, compliant simulated DUT and observer utilities exclusively.
* **Automated Smoke Testing (`smokeit`):** The automated integration test runner (`smokeit`) MUST NOT embed, spawn, or execute any custom device simulation logic or programmatic inline mock devices. It MUST use the standard, compliant simulated DUT client for verifying device connectivity, handshakes, and baseline integration.
* **Automatic Audit Verification:** Automated compliance verifiers and audits (e.g., `AUDIT.md`) MUST verify the strict cleanliness of the `bin/` directory and codebase. The presence of any custom simulated device clients or additional executable files beyond the four core tools (`butler`, `setup`, `verifier`, `smokeit`) constitutes an immediate and fatal protocol compliance violation.

### 6.2 CLI Compatibility Note
To ensure interoperability, implementations MUST correctly handle the transition from positional to optional arguments. A common pitfall is allowing an optional `[conn_spec]` to consume the first required positional argument (e.g., `site_id`). Implementations MUST inspect the first positional argument and, if it does not match a valid connection schema (e.g., starting with `mqtt://`, `mqtts://`, or `pubsub://`), treat it as the first functional argument of the tool.

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
Consistent log prefixes and formats are essential for multi-implementation integration testing. These messages MUST be printed exactly as specified, without additional prefixes (e.g., timestamps or thread IDs) that might interfere with automated log analysis. Log parsers expect exact stdout strings to assert compliance, and any additional runtime prefixes cause false-positive failures. To avoid these failures, all standard compliance logs MUST be printed to stdout completely unprefixed, exactly matching the target formats.

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

The fourth tier of the system verification pipeline builds directly on top of the generic UUFI development environment (reusing Scope 1: Infrastructure and Scope 2: Simulated DUT from the authoritative communication bus specification). It replaces the low-level UUFI test client with the **Butler Orchestrator**, executing a complete state-based firmware update and rollback orchestration cycle over the active broker.

To ensure that multiple disparate implementations can be run side-by-side using the same shared installation without conflicts, created systems MUST be run independently in their respective local directories. This requires:
1. **Model Cloning:** Copy the pre-existing test site model from the standard source site model directory into your local workspace testing directory.
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
   - **Static Port Handshake and Fail-Fast Verification:** During broker startup, the setup script or test runner MUST perform a socket-scanning check to ensure the calculated port is unoccupied by another daemon process on the host. Because the systematically calculated branch-hash port is the absolute single source of truth for all test orchestrators and verifiers, dynamic port shifting, negotiation, or upward scanning on collision is strictly prohibited. If a port collision is detected, the setup utility MUST immediately abort and fail-fast with a clear error, allowing the environment or stale processes to be resolved properly.
3. **Working Directory Execution:** Execute all base setup and communication bus commands using the standard utilities provided by the communication bus repository.

### 10.1. Local Environment Preparation

#### 10.1.1. Automatic Environment & Pip Requirement Validation
Before copying the site model or configuring resources, the setup pipeline MUST validate the Python runtime environment. If a virtual environment (`venv`) is not currently active, it should be activated or created. Additionally, we must verify that all packages specified in the requirements file (`butler/requirements.txt`) are fully satisfied; if they are not, they must be automatically installed using `pip`.
The validation sequence MUST dynamically establish the Python virtual environment and automatically execute `pip install` to satisfy dependency requirements prior to executing setup utilities.
To prevent breaking changes and ensure inter-implementation stability, all implementations MUST strictly pin their dependencies in `butler/requirements.txt` to the following standard versions:
- `paho-mqtt==1.6.1` (to guarantee consistent Client instantiation APIs without callback version errors).
- `google-cloud-storage==2.7.0` (or another verified standard stable release).
No implementation is permitted to use newer major versions of these libraries (such as `paho-mqtt` 2.x) that introduce breaking changes to the baseline communication interfaces.

If the `--offline` flag is provided to the setup utility, it MUST NOT attempt to make remote network calls for package verification or installation. Instead, it must either perform package verification using local caches or ignore missing dependencies to guarantee safe, warning-free offline execution inside hermetic test sandboxes without experiencing download retry latencies.

#### 10.1.2. Isolated Site Model Setup
Establish an isolated copy of the pre-existing test site model by copying the model from the standard source site model directory into the local workspace testing directory.

#### 10.1.3. Base Infrastructure Setup (Delegated to UUFI Section 9)
All base infrastructure lifecycle management—including initializing local MQTT brokers, executing gateway processors, starting simulated devices (DUT), populating tagabases, and triggering site model updates—MUST follow the standard CLI parameter formatting and dynamic port rules codified in the authoritative communication bus specification.

<!-- ASSUMPTION: Local, non-privileged (no-sudo) execution mode is automatically triggered and configured based on the presence of an explicit, custom MQTT port specification in the connection specification (e.g., //mqtt/localhost:$port). All scripts and utilities MUST dynamically derive this non-privileged execution state from the port specification itself and automatically redirect all generated configurations, log outputs, and PID tracking to relative, git-ignored user-space locations (such as var/mosquitto). -->
Local, non-privileged (no-sudo) execution mode MUST be automatically triggered and configured based on the presence of an explicit, custom MQTT port specification in the connection specification (such as `//mqtt/localhost:$port`). All scripts and utilities MUST dynamically derive this non-privileged execution state from the port specification itself and automatically redirect all generated configurations, log outputs, and PID tracking to relative, git-ignored user-space locations (e.g., `var/mosquitto` and `out/`).

Butler's local setup utility (`bin/setup`) acts purely as a wrapper to delegate these base setup commands to the standard communication bus utilities:
1. **Directory Layout Verification:** Confirm that the standard communication bus folder exists directly, raising an immediate hard failure if missing.
2. **Infrastructure Initialization:** Invoke the standard local infrastructure start utility and registration utility using standard connection specifications (e.g. `//mqtt/localhost:$port`) as specified in the authoritative communication bus specification.
3. **Hermetic Teardown (`--stop` flag):** Teardown background processes by delegating stop commands directly to the official communication bus setup utilities.
4. **Prohibition of Embedded/Bundled Third-Party Daemons:** Implementation repositories MUST NOT package, ship, or distribute pre-compiled third-party daemons (such as `mosquitto`, `etcd`, or `influxd`) in their source trees. All required infrastructure services must only be managed, started, and stopped using the official black-box communication bus utilities, avoiding direct path-resolution or system-wide shared library configurations in local test runners.

### 10.2. Starting the Butler Orchestrator
Launch the core Butler orchestrator executable (`bin/butler <conn_spec>`). It MUST connect to the running MQTT broker on the specified connection path and act as the authoritative Cloud Model Server on the UUFI bus.

### 10.3. Starting the Independent Verifier
Run the verifier executable (`bin/verifier <conn_spec>`) to observe topic traffic and actively assert state machine transitions.

### 10.4. Executing Device Simulation and Triggers (Delegated to UUFI Section 9)
For device simulation and traffic generation:
- Launch the simulated Device Under Test (DUT) using the standard simulated DUT utility with the standard CLI parameters.
- Trigger managed model updates using the standard site model trigger utility with the standard CLI parameters.

### 10.6. Running Automated Smoke Tests
To execute a fully automated, non-interactive integration run of Scope 4 (verifying the entire setup, registration, update, rollback, and verification lifecycle), execute the `smokeit` test utility pointing to the dynamically resolved branch-specific port.

### 10.7. Automated Smoke Test Specifications
Any automated integration test harness (such as `bin/smokeit`) MUST adhere to the following strict operational requirements to ensure reliable, isolated side-by-side executions:
1. **MQTT Event Loop Activation:** Every MQTT client instance instantiated by the test harness (including log-reading watchers and cloud-mutation triggers) MUST run a background network event loop (via `loop_start()` or equivalent) to actively read and process incoming broker packets (such as QoS=1 `PUBACK` confirmations). Clients MUST NOT call `wait_for_publish()` without an active event loop running, to prevent execution hangs.
2. **Working Directory and Log Path Resolution:** The test harness MUST execute all external utilities (including `start_dut` or `pubber`) with the working directory explicitly set to the isolated workspace root. Because external utilities write their log outputs (specifically `pubber.log`) relative to their execution working directory, the test harness MUST resolve and monitor the log file path relative to its own local execution directory (e.g., `out/pubber.log` under the workspace root), rather than reading from any global or shared directories (such as the cloned `impl/udmi` folder), ensuring complete isolation of side-by-side test runs.
3. **UDMIS Startup Synchronization & Parallel Daemon Bootstrapping:** The test harness MUST implement a startup synchronization delay (e.g., waiting for `pod_ready.txt` or a standard timeout of at least 15 seconds) after starting the local UDMIS service pod and BEFORE launching the simulated device (DUT), ensuring all dynamic security roles and MQTT subscriptions are active before the client-initiated handshake begins. To optimize execution latency and minimize overall integration test times, the test harness (such as `bin/smokeit`) MUST spin up the Butler Orchestrator and Verifier concurrently in parallel threads or background processes while this synchronization delay is running, rather than waiting for the synchronization period to completely finish before starting those daemons.
4. **Base Infrastructure Teardown and Cleanup Delegation:** All process management, daemon control, and teardown of background services—including local MQTT brokers and other test-associated daemons—MUST be relegated completely to the official UDMI specifications and setup tools (such as calling the stop command on the setup utilities). The test harness and associated scripts MUST NOT employ low-level process management mechanisms, signal handling traps, or local PID file tracking. Instead, they must invoke the official black-box UDMI/UUFI utilities to perform hermetic teardowns.
5. **Dynamic Reflector Mapping:** The UDMIS reflector component MUST utilize dynamic configurations based on local environment variables to bypass hardcoded cloud model dependencies, enhancing portability across different testing environments.
6. **Programmatic Verification and Failure Assertions:** The automated test harness (specifically `bin/smokeit`) MUST NOT act as a blind process scheduler that unconditionally exits with success. It MUST actively verify that the system successfully completed the handshake, transitioned from a pending state to a success state, and reached its terminal stable state. It MUST NOT exit with a successful status if any sub-process crashed, failed to authorize, or if the verifier and butler failed to complete the update lifecycle within a designated timeout. Specifically:
   - **Active Log Auditing:** The test harness MUST actively inspect the active verifier compliance log, the butler log, or the active UUFI message stream during execution.
   - **String-Pattern Assertions:** The test harness MUST assert that the logs contain the exact spec-compliant pattern matching `VERIFIER [INFO]: State transition for.*pending -> success` or `[butler] Device.*terminal state success with version`.
   - **Timeout Enforcement:** The test harness MUST enforce a timeout (minimum 60 seconds) for the update cycle to complete. If the update does not progress to success within this period, it MUST be treated as a functional test failure.
   - **Fail-Fast Exit Status:** On any assertion failure, timeout, or background service early termination, the test harness MUST write a diagnostic error to standard error and exit with a non-zero status code (specifically `exit 1`), ensuring that any integration failures are reliably detected before running external cross-testing or build staging commands.

## 11. Principal Suffix Standardization
To ensure consistency across multiple implementations and avoid custom differentiator mismatches during handshake verification and log analysis, all system entities MUST adhere to a standardized principal naming schema.

### 11.1. Principal Structure
Every system component, tool, or utility MUST resolve and report its principal identity using the dot-separated format:
`{implementation_id}.{entity_suffix}`

Where:
*   `{implementation_id}` represents the unique identifier of the specific implementation run or branch (e.g., `impl_A`, `impl_B`, `impl_C`, `impl_D`). To ensure that a verifier from one implementation (e.g., `impl_A`) can properly validate a butler from another implementation (e.g., `impl_B`) on a shared broker under the cross-test matrix, **each implementation MUST statically or locally determine its own unique `{implementation_id}` (such as hardcoding it to `impl_A`, `impl_B`, `impl_C`, or `impl_D` respectively)**. Implementations MUST NOT attempt to dynamically extract their `{implementation_id}` from the shared `<conn_spec>` userinfo or username segment, as this would cause both the verifier and target butler to report identical principals under a shared connection, breaking cross-test isolation.
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
Handshake protocol requests (Step 1) and replies (Step 2) published over the UUFI bus MUST utilize the standard flattened format where the `"setup"` and `"reply"` payload blocks reside directly at the payload root. Wrapping or nesting these blocks inside a `"udmi"` root sub-object is strictly prohibited and MUST be rejected as non-compliant. To support request-response correlation on shared handshake channels (such as `/uufi/c/config/udmi`), the handshake configuration reply message's envelope MUST include a `"transactionId"` (or `"transaction_id"`) attribute containing the exact transaction ID value from the client's handshake request envelope. Clients MUST verify this transaction ID and reject any handshake replies that do not match their original request's transaction ID.

### 12.2. Subsystem State and Catalog Model Alignment
Simulated devices and DUTs MUST report their actual software and firmware states using the subsystem ID `"system"` or `"pubber_module"` (rather than `"main"` or other custom names) to match the expected version configured under that subsystem. Sourcing, parsing, or extracting of actual software versions MUST be done strictly from the standard `"system.software.<subsystem>"` path of the device state payload (specifically `"system.software.system"` for the system subsystem, or `"system.software.pubber_module"` for the pubber_module subsystem). Sourcing or parsing actual versions from any other path (such as `"blobset.blobs.<subsystem>.version"`) is strictly prohibited. Local Butler components are concerned strictly with identifying the configured subsystem name (supporting both `"system"` and `"pubber_module"`) and extracting the corresponding values from these standard schema-defined locations.
To ensure strict protocol safety and prevent specification divergence, all Butler and Verifier implementations MUST enforce strict compliance with the authoritative UDMI JSON schemas (specifically `state_system_software.json`). Software subsystem states MUST be parsed exclusively as valid JSON objects containing `"version"` and `"status"` attributes. Any raw string representations (e.g., `'system': 'v1'`) MUST be rejected as non-schema-compliant protocol violations.

### 12.3. No Custom Version Field in Blobset Payloads
To ensure strict compliance with the authoritative UDMI schemas (such as `config_blobset_blob.json` and `state_blobset_blob.json`) which utilize `"additionalProperties": false`, all configuration and state messages MUST NOT include any custom `"version"` attribute inside the `"blobset"` blocks (such as `blobset.blobs.<subsystem>.version`). The orchestrator must publish the target/expected version exclusively under the standard `"system.software.<subsystem>"` path of the configuration message. Any `"blobset"` update config command published by the orchestrator must strictly adhere to the standard schema layout (containing only `"url"`, `"sha256"`, `"phase"`, and `"generation"` as defined in `config_blobset_blob.json`).

### 12.4. Envelope Nonce Attribute
To support robust message deduplication and replay protection, clients and devices publishing state, event, or model messages over the UUFI bus SHOULD include a `"nonce"` field in the root of the message's envelope containing a secure, pseudorandomly generated hexadecimal string (at least 32 characters, e.g. 16 bytes). Compliant orchestrators and verifiers MUST gracefully accept, parse, and process envelopes containing the `"nonce"` attribute.

### 12.5. Cloud Model Update Payload Structure
Cloud model updates published over the UUFI bus (specifically on `/uufi/c/model/cloud` or model update channels) MUST utilize the standard flattened format where the `"registries"` key resides directly at the payload root (following the schema defined in the authoritative communication bus specification). Sourcing updates from `/uufi/c/config/cloud` is prohibited. Wrapping or nesting the update payload inside a `"cloud"` root sub-object is strictly prohibited and MUST be rejected as non-compliant.

### 12.6. Single Method for Expected Version Configuration
The expected/desired version of a device's software subsystem MUST be configured in exactly one way: under the standard software dictionary structure within the device's system configuration (e.g., `system.software.<subsystem> = "{version}"`, where `<subsystem>` defaults to `"system"` but can also be `"pubber_module"`). Any alternative or custom configuration properties, such as `"target_version"` (e.g., `system.target_version = "{version}"`), are strictly prohibited and MUST NOT be accepted by the orchestrator or processed as valid expected versions.

### 12.7. Topic Suffix Standard Formatting
To maintain strict compliance with the UUFI topic routing specification, all UUFI topic paths MUST include both a subtype and a subfolder segment, formatted strictly as `/c/{subtype}/{subfolder}`. Omitting the subfolder segment or formatting topic suffixes as `/c/{subtype}` is non-compliant. For standard registry-less handshakes, the subfolder segment MUST be explicitly set to `"udmi"` (e.g., `/uufi/c/state/udmi` and `/uufi/c/config/udmi`). Topic building and routing components MUST NOT generate topic paths lacking either segment.

### 12.8. Isolated Message-Based Handshake Gating and Idempotent Simulation
To support execution inside isolated, non-shared-filesystem sandbox and container environments, all orchestrator, verifier, and simulated device (Pubber) components MUST communicate exclusively over the network message bus (MQTT/UUFI). 
* **Message-Based Handshake Gating:** The verifier MUST publish the handshake outcome directly over the message bus on `/uufi/c/state/udmi`. If a handshake fails or times out, the test coordinator or orchestrator MUST immediately abort the sequence, completely skipping any software update transition or trigger phases to maximize execution speed and minimize testing latency.
* **Bus-Level Error Signaling:** Any component detecting a fatal runtime, connection, or protocol violation error MUST immediately publish a standard validation event on `{prefix}/uufi/r/{site_id}/d/{device_id}/c/events/validation` with `"level": "ERROR"`. The test harness or observer subscribing to this channel must immediately fail-fast and terminate upon receiving any such error.
* **Idempotent Device Simulation:** To minimize JVM boot latencies and reflect realistic hardware deployment models, the simulated device (Pubber / DUT) client MUST be treated as a long-running, idempotent simulation client. It is started once at the beginning of a test track and remains connected across multiple target butler starts and restarts, cleanly processing new configuration payloads as they arrive over the bus.

### 12.9. Robust Incoming Envelope Parsing and Defect Tolerance
To ensure compatibility with standard external UDMI/UUFI tools (such as `site_trigger` and the UDMIS Reflector) which may omit some of the standard MQTT envelope attributes, orchestrators and verifiers MUST NOT reject incoming MQTT envelopes solely because they are missing the `"projectId"` or `"principal"` attribute. Instead, receivers MUST gracefully handle missing envelope fields, using standard default values or fallbacks:
* **Missing projectId Fallback:** If the `"projectId"` attribute is missing from an incoming envelope, it MUST default to `"vibrant"` (or the configured project/prefix).
* **Missing principal Fallback:** If the `"principal"` attribute is missing from an incoming envelope, it MUST be treated as absent, or fall back to utilizing the `"source"` attribute if present.
Outgoing messages published by the orchestrator or verifier, however, MUST still strictly include all standard UUFI-mandated envelope fields to preserve specification compliance.

### 12.10. Static Metadata Sourcing (Make & Model)
Sourcing `make` and `model` dynamically from device state reports is unreliable, as these are static metadata/catalog attributes and are not present inside standard subsystem software state reports. Butler implementations MUST resolve device `make` and `model` statically by looking up the device ID within the Software Catalog (`model.json` or local device metadata files) rather than parsing them from the device state payload. This ensures correct package resolution during update triggers.

### 12.11. Trace-Level Transaction ID Tracking
Limiting transaction ID verification strictly to the handshake phase prevents end-to-end trace correlation of subsequent configuration, state, and telemetry messages. Symmetrically track and propagate the `transactionId` attribute in message envelopes across **all** exchanges (handshakes, model updates, state reports, and configurations) to support trace-level correlation and message deduplication throughout the entire operational sequence.

## 13. Compliance Matrix

| Requirement ID | Component | Description & Verifiable Assertion | Verification Method |
| :--- | :--- | :--- | :--- |
| **REQ-CONN-001** | Connection | All tools must support the `<conn_spec>` argument (e.g., `mqtts://localhost`) both as a positional first argument and via an explicit `--conn_spec` flag. | Static Code Review / CLI Unit Test |
| **REQ-CONN-002** | Connection | If no connection spec is provided, tools must default to a secure connection specification format using `mqtts://<branchname>@localhost/`. Under no circumstances shall tools default to an unencrypted `mqtt://` connection scheme. | Integration / Smoke Test |
| **REQ-CONN-003** | Connection | Robust Connection Spec Parsing: Implementations MUST correctly split and separate the username and password from the connection specification URL (e.g., `mqtts://username:password@host:port/`). They MUST NOT include the password or password delimiter (`:`) in any client IDs, reported principals, or derived project spec strings. If credentials are empty or match the branch name/implementation ID, they MUST default to `rocket` and `monkey` as the credentials. | Unit / Integration Test |
| **REQ-HSK-001** | Handshake | Handshake Request and Reply Payload Formatting: Handshake requests (Step 1) and replies (Step 2) published over the UUFI bus MUST utilize the standard flattened format where the `"setup"` and `"reply"` payload blocks reside directly at the payload root. Symmetrically reject any payloads wrapping or nesting these blocks inside a `"udmi"` root sub-object. Symmetrically track and propagate the `transactionId` attribute in message envelopes across all exchanges. Handshake configuration replies must contain a `"transactionId"` (or `"transaction_id"`) matching the request. | Schema Validation / Mock Test |
| **REQ-HSK-002** | Handshake | Principal Naming Schema: Every system entity must resolve and report its principal identity using the dot-separated format: `{implementation_id}.{entity_suffix}` (e.g., `impl_B.verifier`). Trailing suffixes (like `@` or credentials) are strict protocol violations. Each implementation MUST statically determine its own unique `{implementation_id}` (such as hardcoding it to `impl_A`, `impl_B`, `impl_C`, or `impl_D` respectively). Implementations MUST NOT dynamically extract `{implementation_id}` from the `<conn_spec>` userinfo. | Handshake / Log Verification |
| **REQ-TST-001** | Test Runner | Automated integration test runner (`smokeit`) must not embed, spawn, or execute any custom device simulation logic. It must use the standard, compliant simulated DUT client exclusively. | Directory Audit |
| **REQ-TST-002** | Test Runner | Sudo/Privilege Isolation: Setup and test execution must run in non-privileged (no-sudo) user-space. This pathway is automatically triggered purely by the presence of an explicit, custom MQTT port specification in the connection specification. | Process Audit |
| **REQ-SUB-001** | Subsystem | Subsystem State and Catalog Model Alignment: Devices report actual software and firmware states under the `"system.software.<subsystem>"` path (supporting `"system"` or `"pubber_module"`). Auditing components and verifiers MUST NOT parse actual versions from any other path (such as `"blobset.blobs.<subsystem>.version"`). All Butler and Verifier implementations MUST enforce strict compliance with authoritative UDMI JSON schemas (`state_system_software.json`). Software subsystem states MUST be parsed exclusively as valid JSON objects containing `"version"` and `"status"`. Raw string representations MUST be rejected. | Schema Validation / Integration Test |
| **REQ-BLB-001** | Blobset | No Custom Version Field in Blobset Payloads: All configuration and state messages MUST NOT include any custom `"version"` attribute inside `"blobset"` blocks. The orchestrator must publish the target/expected version exclusively under the standard `"system.software.<subsystem>"` path of the configuration message. Any `"blobset"` update config command published by the orchestrator must strictly contain only `"url"`, `"sha256"`, `"phase"`, and `"generation"`. | Schema Validation / Mock Test |
| **REQ-ENV-001** | Envelope | Envelope Nonce Attribute: Outgoing state, event, or model messages published over the UUFI bus SHOULD include a `"nonce"` field in the envelope root containing a secure, pseudorandomly generated hexadecimal string of at least 32 characters. Receivers (orchestrators and verifiers) MUST gracefully accept and process envelopes with `"nonce"` present. | Integration Test |
| **REQ-CLD-001** | Cloud Model | Cloud Model Update Payload Structure: Cloud model updates published over the UUFI bus utilize the standard flattened format where the `"registries"` key resides directly at the payload root. Wrapping or nesting the update payload inside a `"cloud"` root sub-object is strictly prohibited. Sourcing updates from `/uufi/c/config/cloud` is prohibited. | Schema Validation / Mock Test |
| **REQ-CFG-001** | Configuration | Single Method for Expected Version Configuration: Ensure the expected version is configured ONLY under the standard software dictionary structure (`system.software.<subsystem> = "{version}"`). Custom configuration properties like `system.target_version` are strictly prohibited and MUST NOT be processed as valid expected versions. | Static Code Review / Integration Test |
| **REQ-TPC-001** | Topic | Topic Suffix Standard Formatting: All UUFI topic paths MUST include both a subtype and a subfolder segment, formatted strictly as `/c/{subtype}/{subfolder}` (e.g., `/uufi/c/state/udmi` and `/uufi/c/config/udmi` for standard registry-less handshakes). Generating topic paths lacking either segment is non-compliant. | Static Code Review / Log Verification |
| **REQ-GAT-001** | Handshake | Isolated Message-Based Handshake Gating: Orchestrator, verifier, and simulated device components MUST communicate exclusively over the network message bus (MQTT/UUFI). Verifiers MUST publish handshake outcomes on `/uufi/c/state/udmi`, and any fatal connection or protocol violation must be reported as a validation event with `"level": "ERROR"` on `{prefix}/uufi/r/{site_id}/d/{device_id}/c/events/validation`. | Integration Test |
| **REQ-ENV-002** | Envelope | Robust Incoming Envelope Parsing and Defect Tolerance: Receivers MUST NOT reject incoming MQTT envelopes solely because they are missing the `"projectId"` or `"principal"` attribute. If missing, `"projectId"` MUST default to `"vibrant"` (or the configured project), and `"principal"` MUST fall back to utilizing `"source"` or be treated as absent. | Parser Unit Test |
| **REQ-MET-001** | Metadata | Static Metadata Sourcing (Make & Model): Sourcing `make` and `model` dynamically from device state reports is unreliable. Butler implementations MUST resolve device `make` and `model` statically by looking up the device ID within the Software Catalog (`model.json` or local device metadata files) rather than parsing them from the device state payload. | Integration Test |
| **REQ-TRC-001** | Traceability | Trace-Level Transaction ID Tracking: Symmetrically track and propagate the `transactionId` attribute in message envelopes across all exchanges (handshakes, model updates, state reports, and configurations) to support trace-level correlation and message deduplication throughout the entire operational sequence. | Integration Test |
| **REQ-ENV-003** | Envelope | Envelope Key Standardization: Implementations MUST adhere to strict envelope schema rules: `subType` attribute MUST NOT be included in device state (`state`) or command/config (`config`) messages where topic or context determines it; `deviceRegistryId` MUST NOT be populated where registry identity is already fully implied by the topic path. | Integration Test |
| **REQ-DED-001** | Deduplication | Robust Message Deduplication: The message deduplication cache tracks message `transaction_id` (or `transactionId` in envelope) for at least 5 minutes. Transaction IDs are processed as arbitrary string values. This deduplication filter applies only to incoming Model Update and Command/Config messages; under no circumstances should the filter discard or skip incoming Device State reports. | Integration / Unit Test |
| **REQ-LOG-001** | Logging | Standardized Compliance Logging: Verifier state transitions MUST be output exactly as `VERIFIER [INFO]: State transition for {site_id}/{device_id}/{blob_id}: {old_state} -> {new_state}`. Handshake events MUST be output exactly as `VERIFIER [INFO]: Handshake {started|completed} for {principal}`. Validation errors MUST be output exactly as `VERIFIER [ERROR]: VALIDATION ERROR: {message}`. Terminal states MUST be output exactly as `[butler] Device {site_id}/{device_id}/{blob_id} terminal state {status} with version {version}`. Logs must be clean, unprefixed on stdout. | Integration / Log Verification |
| **REQ-SEQ-001** | Concurrency | Verifier Sequential Processing: Verifier processes messages from the same device/blob_id sequentially (e.g., utilizing a thread-safe message queue) to maintain strict state accuracy and avoid false-positive transition violations. | Concurrency / Integration Test |

