# Butler System Orchestrator

The **Butler** is a declarative, state-based fleet management engine for device firmware updates. It coordinates updates across a fleet of devices by managing state machines for each device/subsystem pair using the UUFI interface.

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
- **spec/**: Formal system specifications (including `uufi.md` and `butler.md`).
- **bin/**: Operational executables and tooling.
- **butler/**: Core Python implementation logic.
- **README.md**: System overview and documentation.

### Operational and Test Artifacts
- **impl/**: Cross-implementation testing workspace (including `test_summary.txt`).
- **tmp/**: Temporary workspace (ephemeral).
- **testing/**: Test assets and environment.
- **venv/**: Python virtual environment.

## 2. Role and Behavior

### 2.1 Orchestrator Behavior
The **Butler** is the primary authority for the `lkg_version` in the cloud model and MUST NOT trust a device-reported `lkg_version` if it conflicts with a previously validated state. To prevent split-brain conditions, the Butler is the **sole authoritative Cloud Model Server** on the UUFI bus; other components (e.g., `mocket`, `verifier`) MUST NOT respond to `query/cloud` messages or unilaterally publish `config/cloud` messages.
- **Discovery:** The Butler MUST dynamically discover registries and devices from incoming state reports or cloud updates. This includes the Handshake Step 1 state message, which MUST be used to populate the initial model entry for a device.
- **Handshake Compliance:** The Butler MUST NOT initiate its own handshake; it MUST instead respond to handshake state messages from Devices and Verifiers with the appropriate config reply as defined in UUFI Section 3.
- **State Machine:**
  - `quiescent`: Target Version == Current Version.
  - `active`: Target Version != Current Version (Transitional state).
  - `pending`: Update in progress (device has received command).

- **Terminal States:** For the purpose of the orchestrator state machine, ONLY `success`, `failure`, and `quiescent` are considered terminal states. The `active` state MUST NOT be considered terminal and MUST trigger a reconciliation attempt if no update is already `pending`.
- **Triggering:** The orchestrator re-evaluates state upon receiving device status reports. A null `current_version` is treated as `0.0.0` (see UUFI Section 8.4).
- **Efficiency:** State transitions and model updates MUST be processed immediately upon receipt of relevant messages to minimize end-to-end latency. Implementations MUST NOT introduce any artificial delay or "settling time" before processing a state change or triggering a reconciliation.
- **Timeout:** The Butler MUST wait for at least `BUTLER_TIMEOUT` (default: 60s) for a device to progress from the `pending` state before triggering a rollback.

### 2.2 Model and Update Management
- **LKG Management:** Upon receiving a device report indicating a successful update where the `current_version` matches the `target_version`, the Butler MUST update the cloud model's `current_version` and `lkg_version`.
- **Persistence:** The Butler MUST update the local model file whenever the cloud model state changes.

## 3. Functional Components

### 3.1 Blob Repository
- **Structure:** `{base_dir}/{make}/{model}/{subsystem_id}/{version}/`
- **Contents:**
  - `bundle.bin`: The binary blob content.
  - `sha256.txt`: Hex-encoded SHA-256 hash of `bundle.bin`.
- **Integrity:** Every blob requires a SHA256 hash for verification.

### 3.2 Model Repository (Desired State)
- **Format:** The cloud model MUST follow the full schema defined in UUFI Section 5.
- **Path Override:** `BUTLER_MODEL_FILE`.
- **Atomicity:** Updates to the local model file MUST be atomic (e.g., write to temporary file then rename).
- **Access:** Direct local access is restricted to `mocket`, `register`, and `trigger`.
- **Primary Key:** Composite of `registry_id` and `device_id`.

### 3.3 Device Conduit (Client-side / Mocket)
- **Reporting:** Periodically publish `current_version`, `status`, and `lkg_version` via `blobset` state messages.
- **Payload Structure:** `blobset` payloads MUST include `make` and `model` fields within the subsystem nesting to ensure the orchestrator can correctly identify the device type. For consistency across implementations, implementations MUST use the `blobs` wrapper key within the `blobset` state report.
- **Lifecycle:** `quiescent` -> `pending` (download/verify) -> `success` or `failure`.
- **Transitions:** Transitions to `success` or `failure` MUST only occur from the `pending` state. A direct transition from `quiescent` to `success` or `failure` is a protocol violation. System and Verifier components MUST ensure that state reports are processed in the order they were generated to avoid false-positive violations.
- **Robustness:** Devices MUST robustly handle immediate state change requests (back-to-back config updates) and ensure eventual consistency with the latest target state.

## 5. Operational Sequences

### 5.1 Update Flow
1. **Initiation:** Model update via `register` or `trigger`.
2. **Status Report:** Device (`mocket`) publishes status in `blobset` state.
3. **Detection:** Butler detects version mismatch, fetches blob metadata.
4. **Command:** Butler publishes `blobset` config payload.
5. **Pending:** Device reports `pending` state and applies update.
6. **Completion:** Device reports `success` and updated `lkg_version` in `blobset` state.
7. **Sync:** Butler sends UUFI `UPDATE` (partial merge) to the cloud model for `current_version` and `lkg_version`.

### 5.2 Rollback Flow
1. **Failure:** Device reports `failure` in `blobset` state.
2. **Fetch LKG:** Butler requests `lkg_version` from the model/mocket.
3. **Reversion:** Butler sends `UPDATE` to the cloud model to revert `target_version` to the `lkg_version`.

## 6. Standard Tooling CLI Interface (bin/)

All tools MUST support the `<conn_spec>` argument (e.g., `mqtt://localhost`). It MUST be supported both as a positional first argument and via an explicit `--conn_spec` flag. On startup, all tools MUST output their connectivity parameters in a consistent format: `Conn spec: scheme={scheme}, host={host}, port={port}, principal={principal}, prefix={prefix}`. This output MUST be directed to `stderr` if the tool is designed to produce machine-readable data on `stdout` (e.g., `observe`).

To ensure interoperability and environmental isolation, tools MUST NOT fail if optional arguments (indicated by `[]`) are omitted, provided a valid default can be determined. When running in a multi-client environment (e.g., parallel testing), implementations MUST strictly adhere to the `Prefix Isolation` requirements defined in UUFI Section 2.2. Specifically, test runners (`smokeit`) MUST incorporate the provided connection prefix into all internally generated topics and child process arguments to prevent cross-trial interference.

- **butler [conn_spec] [-f]**: Starts the system orchestrator.
- **register [conn_spec] [registry_id] <device_id> [make] [model]**: Registers a device in the local model.
- **trigger [conn_spec] [registry_id] <device_id> <subsystem_id> <version> <blob_path>**: Initiates a blobset update process.
- **setup [conn_spec]**: Ensures the local environment (e.g., MQTT broker) is ready.
- **mocket [conn_spec] <registry_id> <device_id> [-f]**: Starts a mock device client.
- **verifier [conn_spec]**: Starts the independent verification tool.
- **observe [conn_spec]**: Passive monitoring of the UUFI bus (output: `{topic}: {payload}`).
- **smokeit [conn_spec]**: Basic integration test.

### 6.1 CLI Compatibility Note
To ensure interoperability, implementations MUST correctly handle the transition from positional to optional arguments. A common pitfall is allowing an optional `[conn_spec]` to consume the first required positional argument (e.g., `registry_id`). Implementations MUST inspect the first positional argument and, if it does not match a valid connection schema (e.g., `mqtt://`), treat it as the first functional argument of the tool.

The startup connectivity output MUST use the resolved numeric port (e.g., `1883`) for the `port` field; it MUST NOT be `None` or empty.

## 7. Standard Configuration Environment Variables

- **`BUTLER_CONN_SPEC`**: Default connection specification URL.
- **`BUTLER_MODEL_FILE`**: Path to local model JSON (default: `testing/model.json`).
- **`BUTLER_BLOBS_DIR`**: Base directory for blobs (default: `testing/blobs`).
- **`BUTLER_TIMEOUT`**: Timeout for `pending` state transitions (default: `60`).
- **`BUTLER_REGISTRY_ID`**: Default registry ID (default: `default`).

## 8. Robustness

- **Idempotency:** All components MUST be idempotent.
- **Deduplication:** Track message `transaction_id` (8-digit hex) for at least 5 minutes.
- **Partial Merge:** `cloud` model `UPDATE` operations MUST be partial merges at the device subsystem level; existing fields not in the payload MUST NOT be modified.

## 9. Verification and Observability

### 9.1 Verifier (Active Observer)
- **Handshake:** MUST complete UUFI handshake.
- **Monitoring:** Track state transitions in the `blobset` subfolder.
- **Reporting:** Publish validation results to `[/{prefix}]/uufi/r/{reg_id}/d/{dev_id}/c/events/validation`. For events related to a specific device, `{reg_id}` and `{dev_id}` MUST match the device. For self-reporting (e.g., handshake status), `{dev_id}` MUST be the verifier's identity (e.g., `verifier`) and `{reg_id}` MUST be `unknown` unless a specific registry has been discovered.
- **Processing:** Verifier components MUST ensure that messages from the same device/subsystem are processed sequentially (e.g., via a message queue) to maintain accurate state tracking and avoid false-positive transition violations.

### 9.2 Observer (Passive Observer)
- **Output:** Raw wire format `{topic}: {payload}`.
- **Constraints:** Unbuffered, exactly one line per message, no truncation. Implementations MUST NOT output any additional text (e.g., connection status, "RECEIVE" labels) beyond the message itself. The startup connectivity output required by Section 6 MUST be directed to `stderr` for the Observer tool to ensure that `stdout` contains only message data. Implementations MUST ensure that message output is thread-safe and that each message is followed by a newline character, even when multiple messages arrive simultaneously.

### 9.3 Compliance Logging
For automated interoperability testing and verification, implementations MUST adhere to the following log formats for critical lifecycle events:
- State Transitions (Verifier): `VERIFIER [INFO]: State transition for {subsystem}: {old_state} -> {new_state}`. The initial state before any report is received MUST be considered `unknown`. To ensure log clarity, verifiers MUST NOT log a transition if the `{new_state}` is identical to the `{old_state}`. This prohibition applies to both the standard output logging and the publication of validation events (Section 9.4) on the UUFI bus.
- Handshake Events (Verifier): `VERIFIER [INFO]: Handshake {started|completed} for {principal}`.
- **Validation Errors (Verifier):** `VERIFIER [ERROR]: VALIDATION ERROR: {message}`.
- **Terminal State (Orchestrator):** `[butler] Device {registry_id}/{device_id}/{subsystem} terminal state {status} with version {version}`. Terminal states MUST include `success`, `failure`, and `quiescent` (even if the version is `0.0.0`). This log MUST be generated whenever a device enters or reports one of these states.
Consistent log prefixes and formats are essential for multi-implementation integration testing. These messages MUST be printed exactly as specified, without additional prefixes (e.g., timestamps or thread IDs) that might interfere with automated log analysis.

### 9.4 Validation Event Schema
- **Topic:** `[/{prefix}]/uufi/r/{registry_id}/d/{device_id}/c/events/validation`
- **Payload:** The `validation` object within the `payload` MUST include:
  - `message`: A human-readable description of the validation event.
  - `level`: One of `INFO`, `WARN`, or `ERROR`.
  - `device_id`: (Optional) The ID of the device being validated.
  - `subsystem_id`: (Optional) The ID of the subsystem being validated.
  - `status`: (Optional) The current state (e.g., `pending`, `success`).
  - `result`: (Optional) One of `pass` or `fail` (case-insensitive).
