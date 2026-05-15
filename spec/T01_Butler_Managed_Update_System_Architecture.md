# Butler Managed Update System Specification

This specification defines the functional requirements and architecture for the Butler Managed Update System, a declarative, state-based fleet management engine for device firmware.

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
- **spec/**: Formal system specifications (including `uufi.md`).
- **bin/**: Operational executables.
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

### UDMI Binding
- **Compliance:** All messages MUST adhere to UUFI schemas.
- **Debug Differentiation:** For singular receiver protocols (e.g., PubSub), append the following to the `user` component:
  - `butler`: (none)
  - `observe`: `.observe`
  - `verifier`: `.verifier`
  - `mocket`: `.mocket`
  - *Example:* `pubsub://admin.verifier@project`

## 2. Functional Components

### 2.1 Blob Repository
- **Structure:** `{make}/{model}/{subsystem}/{version}/`.
- **Integrity:** Every blob requires a SHA256 hash.

### 2.2 Model Repository (Desired State)
- **Path Override:** `BUTLER_MODEL_FILE`.
- **Atomicity:** Updates MUST be atomic (temporary file + rename).
- **Access:** Direct access restricted to `mocket`, `register`, and `trigger`.
- **Primary Key:** Composite of `registry_id` and `device_id`.
- **Internal Storage:** MUST mirror the nested `registries` hierarchy (Section 5.1 of `uufi.md`).
- **Cloud Interface:** Model representation on the bus MUST use the nested structure: `{"registries": { "reg_id": { "devices": { "dev_id": { "subsystem_id": { "target_version": "...", "current_version": "...", "status": "...", "lkg_version": "..." } } } } } }` (wrapped in `cloud` subfolder).

### 2.3 Butler Orchestrator (Control Logic)
- **State Machine:**
  - `quiescent`: Target == Current.
  - `active`: Target != Current.
  - `pending`: Update in progress.
- **Triggering:** Triggered by `mocket` status reports. Null `current_version` is treated as an empty string.
- **Settling Time:** Minimum 5s delay after state changes before re-evaluation.
- **Timeout:** 60s window for `pending` state transitions (respects `BUTLER_TIMEOUT`).
- **Handshake:** MUST complete UUFI handshake within 60s or Fail-fast.
- **Rollback:** On critical failure, revert `target_version` to `lkg_version`.
- **LKG Management:** Butler MUST maintain the Last Known Good (LKG) version. Upon successful update (status `success` or `quiescent` with new version), the Orchestrator MUST update the `lkg_version` in the cloud model.
- **Discovery:** Dynamically discover registries/devices via `[/{prefix}]/uufi/c/...` messages.

### 2.4 Device Conduit (Client-side)
- **Reporting:** Periodically publish `current_version`, `status`, and `lkg_version`.
- **Lifecycle:** `quiescent` -> `pending` (download/verify) -> `success` or `failure`.
- **Transitions:** Transitions to `success` or `failure` MUST only occur from the `pending` state. A direct transition from `quiescent` to `success` or `failure` is a protocol violation.

## 3. Operational Sequences

### 3.1 Update Flow
1. Model update (via `register`/`trigger`).
2. `mocket` publishes status.
3. Butler detects mismatch, fetches blob metadata.
4. Butler publishes `update_payload`.
5. Device reports `pending`, applies update.
6. Device reports `success` and `lkg_version`.
7. Butler sends `UPDATE` (partial merge) to `mocket` for `current_version` and `lkg_version`.

### 3.2 Rollback Flow
1. Device reports `failure`.
2. Butler requests `lkg_version` from `mocket`.
3. Butler sends `UPDATE` to `mocket` to revert `target_version` to `lkg_version`.

## 4. Robustness

- **Idempotency:** Components MUST be idempotent.
- **Deduplication:** Track 8-digit hex `nonce` for 5 minutes.
- **Partial Merge:** `cloud` model `UPDATE` operations MUST NOT overwrite unrelated fields.

## 5. Verification and Observability

### 5.1 Verifier (Active Observer)
- **Handshake:** MUST complete UUFI handshake.
- **Monitoring:** Track state transitions in `update` subfolder.
- **Reporting:** Publish to `[/{prefix}]/uufi/r/{reg_id}/d/{dev_id}/c/events/validation`.
- **Timestamp:** Enforce RFC 3339 minimal precision for Butler messages.

### 5.2 Observer (Passive Observer)
- **Startup:** Output connectivity parameters.
- **Output Format:** `{topic}: {payload}` (Raw wire format).
- **Constraints:** Unbuffered, single line, no truncation.

## 6. Tooling (bin/)

- **smokeit conn_spec**: Basic integration test.
- **setup conn_spec**: Substrate initialization.
- **observe conn_spec**: Passive monitoring.
- **verifier conn_spec**: Active validation.
- **butler conn_spec [-f]**: Orchestrator.
- **mocket conn_spec reg_id dev_id [-f]**: Mock system/device.
- **register reg_id dev_id**: Local model entry.
- **trigger reg_id dev_id version path**: Local model trigger.
