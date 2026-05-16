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
- **Authority:** The Butler is the primary authority for the `lkg_version` in the cloud model and MUST NOT trust a device-reported `lkg_version` if it conflicts with a previously validated state.
- **Discovery:** The Butler MUST dynamically discover registries and devices from incoming state reports or cloud updates. This includes the Handshake Step 1 state message, which SHOULD be used to populate the initial model entry for a device.
- **State Machine:**
  - `quiescent`: Target Version == Current Version.
  - `active`: Target Version != Current Version.
  - `pending`: Update in progress (device has received command).
- **Triggering:** The orchestrator re-evaluates state upon receiving device status reports. A null `current_version` is treated as an empty string.
- **Efficiency:** State transitions and model updates MUST be processed immediately upon receipt of relevant messages to minimize end-to-end latency.
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
- **Transitions:** Transitions to `success` or `failure` MUST only occur from the `pending` state. A direct transition from `quiescent` to `success` or `failure` is a protocol violation.
- **Robustness:** Devices MUST robustly handle immediate state change requests (back-to-back config updates) and ensure eventual consistency with the latest target state.

## 4. Functional Components

### 4.1 Blob Repository
- **Structure:** `{base_dir}/{make}/{model}/{subsystem_id}/{version}/`
- **Contents:**
  - `bundle.bin`: The binary blob content.
  - `sha256.txt`: Hex-encoded SHA-256 hash of `bundle.bin`.
- **Integrity:** Every blob requires a SHA256 hash for verification.

### 4.2 Model Repository (Desired State)
- **Format:** The cloud model MUST follow the full schema defined in UUFI Appendix (A.2), including the top-level `cloud` wrapper and the 3-level nesting (Registries -> devices -> Device -> Subsystem). The `devices` wrapper is mandatory, and no additional nesting (like `subsystems`) is permitted between the device and its subsystems.
- **Path Override:** `BUTLER_MODEL_FILE`.
- **Atomicity:** Updates to the local model file MUST be atomic (e.g., write to temporary file then rename).
- **Access:** Direct local access is restricted to `mocket`, `register`, and `trigger`.
- **Primary Key:** Composite of `registry_id` and `device_id`.

### 4.3 Device Conduit (Client-side / Mocket)
- **Reporting:** Periodically publish `current_version`, `status`, and `lkg_version` via `blobset` state messages.
- **Payload Structure:** `blobset` payloads MUST include `make` and `model` fields within the subsystem nesting to ensure the orchestrator can correctly identify the device type. For consistency across implementations, implementations MUST use the `blobs` wrapper key within the `blobset` state report.
- **Lifecycle:** `quiescent` -> `pending` (download/verify) -> `success` or `failure`.
- **Transitions:** Transitions to `success` or `failure` MUST only occur from the `pending` state. A direct transition from `quiescent` to `success` or `failure` is a protocol violation.

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

All tools MUST support the `<conn_spec>` argument (e.g., `mqtt://localhost`). It MUST be supported both as a positional first argument and via an explicit `--conn_spec` flag.

- **butler [conn_spec] [-f]**: Starts the system orchestrator.
- **register [conn_spec] [registry_id] <device_id> [make] [model]**: Registers a device in the local model.
- **trigger [conn_spec] [registry_id] <device_id> <subsystem_id> <version> <blob_path>**: Initiates a blobset update process.
- **setup [conn_spec]**: Ensures the local environment (e.g., MQTT broker) is ready.
- **mocket [conn_spec] <registry_id> <device_id> [-f]**: Starts a mock device client.
- **verifier [conn_spec]**: Starts the independent verification tool.
- **observe [conn_spec]**: Passive monitoring of the UUFI bus (output: `{topic}: {payload}`).
- **smokeit [conn_spec]**: Basic integration test.

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
- **Reporting:** Publish validation results to `[/{prefix}]/uufi/r/{reg_id}/d/{dev_id}/c/events/validation`. The Verifier MUST use the `deviceRegistryId` provided during its Handshake for its own topic path. For reporting on other devices, if the `registry_id` for that device is unknown, the Verifier MUST use `unknown` as the `{reg_id}` in the topic path.
- **Payload Schema:** The `validation` object within the message payload MUST contain:
  - `message` (string, mandatory): Human-readable event description.
  - `level` (string, mandatory): One of `INFO`, `WARN`, `ERROR`.
  - `result` (string, optional): One of `PASS`, `FAIL`, `INFO`.
  - `device_id` (string, optional): The ID of the device being validated.
  - `subsystem_id` (string, optional): The ID of the subsystem being validated.

### 9.2 Observer (Passive Observer)
- **Output:** Raw wire format `{topic}: {payload}`.
- **Constraints:** Unbuffered, single line, no truncation.

### 9.3 Compliance Logging
For automated interoperability testing and verification, implementations MUST adhere to the following log formats for critical lifecycle events in both STDOUT and the `message` field of `events/validation` payloads:
- **Handshake Step 1 (Client):** `VERIFIER [INFO]: Handshake started by {source}`
- **Handshake Step 2 (System):** `VERIFIER [INFO]: Handshake completed for {client}`
- **State Transitions (Verifier):** `VERIFIER [INFO]: State transition for {subsystem}: {old_state} -> {new_state}`
- **Validation Errors (Verifier):** `VERIFIER [ERROR]: VALIDATION ERROR: {message}`
- **Terminal State (Orchestrator):** `[butler] Device {registry_id}/{device_id}/{subsystem} terminal state {status} with version {version}`
Consistent log prefixes and formats are essential for multi-implementation integration testing.
