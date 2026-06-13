# Butler System Orchestrator

The **Butler** is a declarative, state-based fleet management engine for managed software updates. It coordinates updates across a
fleet of devices by managing a state machine for individual device blob updates using the UUFI interface. UUFI is a message
based interface as part of the UDMI system defined by the relative (from this file) path `../../../udmi/docs/specs/uufi.md`.

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
- **spec/**: Formal system specifications (primarily `butler.md`).
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
- **Handshake Compliance:** Butler MUST NOT initiate its own handshake; it MUST instead respond to handshake state messages from Devices and Verifiers with the appropriate config reply as defined in UUFI.
- **State Machine:**
  - `unknown`: Initial tracking state before any device report is received.
  - `quiescent`: Expected/Desired Version == Actual/Current Version.
  - `active`: Expected/Desired Version != Actual/Current Version (Reconciliation required; triggers an update command).
  - `pending`: Update command has been sent to the device, awaiting dynamic state update showing completion or failure.
  The expected/desired version is sourced over the UUFI bus from the Expected Cloud Model (`system.software.<blob_id> = 'version_tag'` as defined in `model_system.json`), where `<blob_id>` is the identifier of the target software. The actual/current version is dynamically reported by the device under the same path in state messages.

- **Triggering:** The orchestrator re-evaluates state and triggers update commands immediately upon receiving device status reports showing version drift (`expected != actual`), unless the device is already in a `pending` transition.
- **No Rollback:** The Butler MUST NOT manage, track, or trigger rollbacks. If an update fails, the Butler simply reports the terminal state. Any rollback or reversion is the domain of the device itself (internally) or an administrator manually updating the expected version in the immutable `site_model`.
- **Efficiency:** State transitions and version reconciliation MUST be processed immediately upon receipt of relevant state messages to minimize end-to-end latency.

### 2.2 Model and Update Management
- **Local Model (Software Catalog Only):** Sourced from `BUTLER_MODEL_FILE` (default: `udmi_blob_store/model.json`), the on-disk Butler database acts exclusively as a **Software Catalog (Package Metadata Database)**. It MUST NOT store any device-specific information, `lkg_version`, or transient update states. It is strictly used to answer catalog queries (such as *"What potential versions for a particular make/model/blob are available?"* or *"What are the metadata parameters/bits for a particular make/model/blob?"*).

## 3. Functional Components

### 3.1 Blob Repository
- **Structure:** `{base_dir}/{make}/{model}/{blob_id}/{version}/` (where `{blob_id}` is the software or subsystem identifier).
- **Contents:**
  - `bundle.bin`: The binary blob content.
  - `sha256.txt`: Hex-encoded SHA-256 hash of `bundle.bin`.
- **Integrity:** Every blob requires a SHA256 hash for verification.

### 3.2 Model Repository (Desired State)
- **Format:** The cloud model MUST follow the full schema defined in UUFI. The physical model follows the immutable `{site_id}/devices/{device_id}/` directory structure of the UDMI/UUFI site_model, where `{site_id}` is the directory name containing `devices` and represents the site/registry ID.
- **Path Override:** `BUTLER_MODEL_FILE`.
- **Access:** The local model file is a read-only Software Catalog (no Butler CLI tools modify this database at runtime; any dynamic target version changes are processed reactively over the UUFI bus).
- **Primary Key:** Composite of `site_id` (registry ID) and `device_id`.

### 3.3 Device Conduit (Client-side / DUT)
- **Reporting:** Periodically publish the actual/current version (under the standard `system.software.<blob_id>` path), `status`, and `lkg_version` via state messages.
- **Payload Structure:** State reports MUST include `make` and `model` fields within the target `<blob_id>` nesting to ensure the orchestrator can correctly identify the device type. For consistency across implementations, implementations MUST use the `blobs` wrapper key within the `blobset` state report.
- **Lifecycle:** `quiescent` -> `pending` (download/verify) -> `success` or `failure`.
- **Transitions:** Transitions to `success` or `failure` MUST only occur from the `pending` state. A direct transition from `quiescent` to `success` or `failure` is a protocol violation. System and Verifier components MUST ensure that state reports are processed in the order they were generated to avoid false-positive violations.
- **Robustness:** Devices MUST robustly handle immediate state change requests (back-to-back config updates) and ensure eventual consistency with the latest expected/desired target state.

## 5. Operational Sequences

### 5.1 Update Flow
1. **Initiation:** The expected/desired version is updated in the live Cloud Model via the UDMI `site_trigger` utility (Scope 3) which updates the local model file on disk and publishes a corresponding `model/cloud` Model Update message over the UUFI bus, emulating a database update.
2. **Status Report:** The Device (DUT) publishes its actual/current version and status in its State reports.
3. **Detection:** The Butler detects a version mismatch between the expected version (live Cloud Model) and actual version (device state), and queries the Software Catalog (`BUTLER_MODEL_FILE`) to find the available package metadata matching the device's `{make}/{model}/{blob_id}/{version}`.
4. **Command:** The Butler publishes a `blobset` config payload containing the update package URL and validation parameters over the UUFI bus.
5. **Pending:** The Device reports `pending` state and begins downloading/applying the update.
6. **Completion:** The Device reports `success` or `failure` (along with its updated actual version and `lkg_version`) in its state reports, transitioning the active tracking loop. The Butler does NOT orchestrate rollbacks; rollback or reversion is managed internally by the device itself or by subsequent manual modification of the immutable `site_model` target.

## 6. Standard Tooling CLI Interface (bin/)

All tools MUST support the `<conn_spec>` argument (e.g., `mqtt://localhost`). It MUST be supported both as a positional first argument and via an explicit `--conn_spec` flag. On startup, all tools MUST output their connectivity parameters in a consistent format: `Conn spec: scheme={scheme}, host={host}, port={port}, principal={principal}, prefix={prefix}`. This output MUST be directed to `stderr` if the tool is designed to produce machine-readable data on `stdout` (e.g., `observe`).

To ensure interoperability and environmental isolation, tools MUST NOT fail if optional arguments (indicated by `[]`) are omitted, provided a valid default can be determined. When running in a multi-client environment (e.g., parallel testing), implementations MUST strictly adhere to the `Prefix Isolation` requirements defined in UUFI. Specifically, test runners (`smokeit`) MUST incorporate the provided connection prefix into all internally generated topics and child process arguments to prevent cross-trial interference.

These are the ONLY files that should be in the `bin/` directory.

- **butler [conn_spec] [-f]**: Starts the system orchestrator.
- **setup [conn_spec]**: Ensures the local environment (e.g., MQTT broker) is ready.
- **verifier [conn_spec]**: Starts the independent verification tool.
- **observe [conn_spec]**: Passive monitoring of the UUFI bus (output: `{topic}: {payload}`).
- **smokeit [conn_spec]**: Basic integration test.

### 6.1 CLI Compatibility Note
To ensure interoperability, implementations MUST correctly handle the transition from positional to optional arguments. A common pitfall is allowing an optional `[conn_spec]` to consume the first required positional argument (e.g., `site_id`). Implementations MUST inspect the first positional argument and, if it does not match a valid connection schema (e.g., `mqtt://`), treat it as the first functional argument of the tool.

The startup connectivity output MUST use the resolved numeric port (e.g., `1883`) for the `port` field; it MUST NOT be `None` or empty. If a connection string does not specify a path (and thus has no prefix), the `prefix` parameter MUST be output as `None` (e.g., `prefix=None`).

## 7. Standard Configuration Environment Variables

- **`BUTLER_CONN_SPEC`**: Default connection specification URL.
- **`BUTLER_MODEL_FILE`**: Path to local model JSON (default: `udmi_blob_store/model.json`).
- **`BUTLER_BLOBSTORE_PROVIDER`**: Specifies the pluggable BlobStore implementation to use. Supported values are `local` (Reference Local Disk Storage) or `gcs` (GCP Google Cloud Storage). Default: `local`.
- **`BUTLER_BLOBS_DIR`**: Base directory for local packages when using the `local` provider (default: `udmi_blob_store/packages`).
- **`BUTLER_GCS_BUCKET`**: Target Google Cloud Storage bucket name when using the `gcs` provider (e.g., `my-update-bucket`).
- **`BUTLER_TIMEOUT`**: Timeout for `pending` state transitions (default: `60`).
- **`BUTLER_REGISTRY_ID`**: Default site/registry ID (default: `default`).
- **`GOOGLE_APPLICATION_CREDENTIALS`**: Optional path to GCP Service Account JSON key file used by the `gcs` provider to authenticate and sign URLs.

## 8. Robustness

- **Idempotency:** All components MUST be idempotent.
- **Deduplication:** Track message `transaction_id` (or `transactionId` in envelope) for at least 5 minutes. Implementations MUST support tracking transaction IDs as arbitrary string values (which can include 8-digit hex strings, UUIDs, or structured session strings like `UUFI:sess123:001`).
- **Partial Merge:** `cloud` model `UPDATE` operations MUST be partial merges at the device subsystem level; existing fields not in the payload MUST NOT be modified.

## 9. Verification and Observability

### 9.1 Verifier (Active Observer)
- **Handshake:** MUST complete UUFI handshake.
- **Monitoring:** Track state transitions in the `blobset` subfolder.
- **Reporting:** Publish validation results to `[/{prefix}]/uufi/r/{site_id}/d/{device_id}/c/events/validation`. For events related to a specific device, `{site_id}` and `{device_id}` MUST match the device. For self-reporting (e.g., handshake status), `{device_id}` MUST be the verifier's identity (e.g., `verifier`) and `{site_id}` MUST be `unknown` unless a specific site/registry has been discovered.
- **Processing:** Verifier components MUST ensure that messages from the same device/blob_id are processed sequentially (e.g., via a message queue) to maintain accurate state tracking and avoid false-positive transition violations.

### 9.2 Observer (Passive Observer)
- **Output:** Raw wire format `{topic}: {payload}`.
- **Constraints:** Unbuffered, exactly one line per message, no truncation. Implementations MUST NOT output any additional text (e.g., connection status, "RECEIVE" labels) beyond the message itself. The startup connectivity output required by Section 6 MUST be directed to `stderr` for the Observer tool to ensure that `stdout` contains only message data. Implementations MUST ensure that message output is thread-safe and that each message is followed by a newline character, even when multiple messages arrive simultaneously.

### 9.3 Compliance Logging
For automated interoperability testing and verification, implementations MUST adhere to the following log formats for critical lifecycle events:
- State Transitions (Verifier): `VERIFIER [INFO]: State transition for {site_id}/{device_id}/{blob_id}: {old_state} -> {new_state}`. To ensure backward-compatibility with single-device verification parsers, implementations may omit the `{site_id}/{device_id}/` segment if verification is restricted to a single target device. The initial state before any report is received MUST be considered `unknown`. To ensure log clarity, verifiers MUST NOT log a transition if the `{new_state}` is identical to the `{old_state}`. This prohibition applies to both the standard output logging and the publication of validation events (Section 9.4) on the UUFI bus.
- Handshake Events (Verifier): `VERIFIER [INFO]: Handshake {started|completed} for {principal}`.
- **Validation Errors (Verifier):** `VERIFIER [ERROR]: VALIDATION ERROR: {message}`.
- **Terminal State (Orchestrator):** `[butler] Device {site_id}/{device_id}/{blob_id} terminal state {status} with version {version}`. Terminal states MUST include `success`, `failure`, and `quiescent` (even if the version is `"0.0.0"`). This log MUST be generated whenever a device enters or reports one of these states.
Consistent log prefixes and formats are essential for multi-implementation integration testing. These messages MUST be printed exactly as specified, without additional prefixes (e.g., timestamps or thread IDs) that might interfere with automated log analysis.

### 9.4 Validation Event Schema
- **Topic:** `[/{prefix}]/uufi/r/{site_id}/d/{device_id}/c/events/validation`
- **Payload:** The `validation` object within the `payload` MUST include:
  - `message`: A human-readable description of the validation event.
  - `level`: One of `INFO`, `WARN`, or `ERROR`.
  - `device_id`: (Optional) The ID of the device being validated.
  - `blob_id`: (Optional) The ID of the software/subsystem being validated (matches the `system.software.<blob_id>` key).
  - `status`: (Optional) The current state (e.g., `pending`, `success`).
  - `result`: (Optional) One of `pass` or `fail` (case-insensitive).

## 10. Development and Testing Workflow (Scope 4)
<!-- ASSUMPTION: User direct command overrides the general spec edit restrictions of AGENTS.md -->

The fourth tier of the system verification pipeline builds directly on top of the generic UUFI development environment (reusing Scope 1: Infrastructure and Scope 2: Pubber DUT from `uufi.md` Section 9). It replaces the low-level UUFI test client (Scope 3) with the **Butler Orchestrator**, executing a complete state-based firmware update and rollback orchestration cycle over the active broker.

### 10.1. Local Environment Preparation
Ensure that a local UUFI infrastructure (Scope 1) has been started. Then, run the Butler setup utility to initialize local workspace directories, local model files, and other Butler-specific resources:
```bash
bin/setup
```

### 10.2. Starting the Butler Orchestrator
Launch the core Butler orchestrator. It will connect to the running MQTT broker (Scope 1) and act as the authoritative Cloud Model Server on the UUFI bus:
```bash
bin/butler
```

### 10.3. Starting the Independent Verifier
Run the verifier tool in a separate terminal. The verifier will perform its handshake with the Butler and begin passive/active tracking of device state transitions on the UUFI bus:
```bash
bin/verifier
```

### 10.4. Starting the Device Under Test (Pubber DUT)
Launch the simulated on-premise device in a separate terminal using the same command as Scope 2, adjusted for the workspace directory path:
```bash
../udmi/bin/start_dut ../udmi/sites/udmi_site_model mqtt://localhost/ AHU-1 "uufi-serial"
```
*Note:* The Butler orchestrator coordinates managed updates. While **Pubber** connects and handshakes successfully, it may fail to fully execute the specific firmware state transitions (`quiescent` -> `pending` -> `success`/`failure`) that a custom UDMI client might report. Let the tests fail on these steps if Pubber lacks full update state-machine capabilities; this is expected behavior to verify platform readiness.

### 10.5. Triggering a Managed Update (Functional Verification)
Initiate a managed software update by using UDMI's `site_trigger` utility to mutate the physical site model file on disk and publish the dynamic `model/cloud` update event over the UUFI bus:
```bash
../udmi/bin/site_trigger update ../udmi/sites/udmi_site_model AHU-1 system 1.1.0
```

### 10.6. Running Automated Smoke Tests
To execute a fully automated, non-interactive integration run of Scope 4 (verifying the entire setup, registration, update, rollback, and verification lifecycle), run:
```bash
bin/smokeit
```
