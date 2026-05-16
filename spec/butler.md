# Butler System Orchestrator

The **Butler** is a declarative, state-based fleet management engine for device firmware updates. It coordinates updates across a fleet of devices by managing state machines for each device/subsystem pair using the UUFI interface.

## 1. Project Structure

The root directory MUST ONLY contain the following files and directories:

### Immutable Metadata and Procedures
- **AGENTS.md**: Agent-specific instructions and mandates.
- **REBUILD.md**: System rebuild procedures.
- **UPDATE.md**: Maintenance and update procedures.
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
- **test_summary.txt**: Verification and testing results.
- **impl/**: Cross-implementation testing workspace.
- **tmp/**: Temporary workspace (ephemeral).
- **testing/**: Test assets and environment.
- **venv/**: Python virtual environment.

## 2. Communication Substrate

The system utilizes a message-based transport (MQTT or PubSub) as defined in `uufi.md`.

### UUFI Compliance
- **Standardization:** All messages MUST adhere to UUFI schemas and the messaging mechanism defined in `uufi.md`.
- **Handshake Protocol:** The Butler MUST implement the handshake protocol as specified in the UUFI documentation (Section 3). It MUST complete the handshake within 60s or fail-fast.
- **Topic Structure:** All MQTT topics MUST start with a leading slash `/` and adhere to the `/uufi/` prefix structure.
- **Debug Differentiation:** For singular receiver protocols (e.g., PubSub), append identifiers to the `user` component:
  - `butler`: (none)
  - `observe`: `.observe`
  - `verifier`: `.verifier`
  - `mocket`: `.mocket`

## 3. Role and Behavior

### 3.1 Orchestrator Behavior
- **Authority:** The Butler is the primary authority for the `lkg_version` in the cloud model and SHOULD NOT trust a device-reported `lkg_version` if it conflicts with a previously validated state.
- **State Machine:**
  - `quiescent`: Target Version == Current Version.
  - `active`: Target Version != Current Version.
  - `pending`: Update in progress (device has received command).
- **Triggering:** The orchestrator re-evaluates state upon receiving device status reports. A null `current_version` is treated as an empty string.
- **Settling Time:** A minimum 5s delay SHOULD be observed after state changes before re-evaluation to avoid race conditions.
- **Timeout:** The Butler MUST wait for at least `BUTLER_TIMEOUT` (default: 60s) for a device to progress from the `pending` state before triggering a rollback.
- **Discovery:** The Butler MUST dynamically discover registries and devices via the UUFI message bus.

### 3.2 Model Synchronization
- **LKG Management:** Upon receiving a device report indicating a successful update (status `success` or `quiescent`) where the `current_version` matches the `target_version`, the Butler MUST update the cloud model's `current_version` and `lkg_version`.
- **Persistence:** The Butler MUST update the local model file (configured via `BUTLER_MODEL_FILE`) whenever the cloud model state changes.
- **Model Update Robustness:** Any terminal state reporting the new version SHOULD trigger a model synchronization, not just the transient `success` state.
- **Metadata Ingestion:** Orchestrators MUST ingest and cache `make` and `model` information from all available sources (registration, cloud updates, and state reports).

### 3.3 Identity and Differentiators
- **Naming Schemes:** Butler implementations SHOULD NOT detect or reject identities with multiple components (e.g., `user.toolname`) as "manual differentiators" if they are part of a standardized naming scheme.

## 4. Functional Components

### 4.1 Blob Repository
- **Structure:** `{base_dir}/{make}/{model}/{subsystem_id}/{version}/`
- **Contents:**
  - `bundle.bin`: The binary blob content.
  - `sha256.txt`: Hex-encoded SHA-256 hash of `bundle.bin`.
- **Integrity:** Every blob requires a SHA256 hash for verification.

### 4.2 Model Repository (Desired State)
- **Format:** The cloud model MUST follow the full schema defined in UUFI Appendix (A.2), including the top-level `cloud` wrapper and the 3-level nesting (Registries -> Devices -> Subsystems).
- **Path Override:** `BUTLER_MODEL_FILE`.
- **Atomicity:** Updates to the local model file MUST be atomic (e.g., write to temporary file then rename).
- **Access:** Direct local access is restricted to `mocket`, `register`, and `trigger`.
- **Primary Key:** Composite of `registry_id` and `device_id`.

### 4.3 Device Conduit (Client-side / Mocket)
- **Reporting:** Periodically publish `current_version`, `status`, and `lkg_version` via `blobset` state messages.
- **Payload Structure:** `blobset` payloads MUST include `make` and `model` fields within the subsystem nesting to ensure the orchestrator can correctly identify the device type. For consistency across implementations, it is RECOMMENDED to use the `blobs` wrapper key within the `blobset` state report.
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

All tools MUST support the `<conn_spec>` argument (e.g., `mqtt://localhost`).

- **butler <conn_spec> [-f]**: Starts the system orchestrator.
- **register [registry_id] <device_id> [make] [model]**: Registers a device in the local model.
- **trigger [registry_id] <device_id> <subsystem_id> <version> <blob_path>**: Initiates a blobset update process.
- **setup <conn_spec>**: Ensures the local environment (e.g., MQTT broker) is ready.
- **mocket <conn_spec> <registry_id> <device_id> [-f]**: Starts a mock device client.
- **verifier <conn_spec>**: Starts the independent verification tool.
- **observe <conn_spec>**: Passive monitoring of the UUFI bus (output: `{topic}: {payload}`).
- **smokeit <conn_spec>**: Basic integration test.

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
- **Reporting:** Publish validation results to `[/{prefix}]/uufi/r/{reg_id}/d/{dev_id}/c/events/validation`.

### 9.2 Observer (Passive Observer)
- **Output:** Raw wire format `{topic}: {payload}`.
- **Constraints:** Unbuffered, single line, no truncation.
