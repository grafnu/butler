# Multi-Implementation Development and Verification Workflow



---

## 1. Multi-Implementation Architecture

To ensure robustness and compliance with the core architectural specifications, the Butler project maintains multiple independent implementations of the system (typically designated as implementations `A`, `B`, `C`, and `D` located in `impl/A`, `impl/B`, `impl/C`, and `impl/D` respectively). These correspond to the git branches `impl_A`, `impl_B`, `impl_C`, and `impl_D`.

The integration pipeline maintains strict consistency using a three-phase sequence driven by automated helper scripts:
```
           [tools/run_updates]  ====>  [tools/run_cross]  ====>  [tools/run_merger]
```

### Phase Gating

Each phase MUST NOT execute unless the preceding phase completed successfully:

1. **Phase 1 → Phase 2 Gate:** `tools/run_cross` MUST NOT execute if any implementation in Phase 1 (`tools/run_updates`) failed or timed out. `run_updates` exits non-zero if any implementation failed, so chained execution (`run_updates && run_cross`) enforces this gate automatically.
2. **Phase 2 → Phase 3 Gate:** `tools/run_merger` MUST NOT execute if `impl/test_summary.txt` is missing, empty, or contains only `FAIL` entries (indicating infrastructure failure rather than spec issues). If all pairings failed, the merger agent cannot distinguish genuine interop defects from environmental failures.

### Timing Model

The cross-implementation test harness uses event-driven polling instead of fixed sleeps:
- **Handshake Wait:** Polls for `Handshake completed` in the verifier log up to `CROSS_HANDSHAKE_TIMEOUT` seconds (default: 30s).
- **Transition Wait:** Polls for `terminal state` in the butler log up to `CROSS_TRANSITION_TIMEOUT` seconds (default: 240s). This accommodates the full `BUTLER_TIMEOUT` retry sequence specified in `spec/butler.md` Section 2.1: an initial 60s timeout plus up to 3 retry attempts spaced 60s apart (worst case: 60 + 3×60 = 240s).
- Both timeouts are configurable via environment variables.
- **Per-Implementation Agent Timeout:** Each implementation's `@UPDATE.md` agent run is bounded by `IMPL_TIME` (default: 20 minutes). If an agent times out or crashes, the harness writes a `spec_analysis.md` containing `"PROCESSING ERROR"` (not a genuine spec analysis) to signal the failure. The Merger Agent MUST distinguish these processing errors from real spec-level feedback and not act on them as spec issues.

## 2. Phase 1: Implementation Synchronization and Updates (`tools/run_updates`)

The first phase ensures that all implementation branches are fully updated, checked out, and synchronized with the core specification changes on the parent `main` branch.

### Command and Usage:
```bash
./tools/run_updates [options]
```

### Key Options:
- `-p` or `--parallel`: Runs the update agent workflows for all implementations concurrently in the background rather than sequentially.
- `-s` or `--sandbox`: Enables sandbox execution mode for the developer agent.

### Operational Sequence:
1. **Repository Synchronization**:
   - Reads `udmi_version.txt` at the root of the workspace to determine the authoritative repository URL and commit/branch target for the UDMI standard dependencies.
   - For each implementation (`A`, `B`, `C`, `D`), clones or checks out the respective implementation branch (`impl_A`, `impl_B`, etc.) into `impl/<ID>`.
   - Clones and pins the exact UDMI version under `impl/<ID>/impl/udmi` in each sub-workspace.
   - Verifies that all cloned sub-workspaces are perfectly synchronized to the same UDMI reference to prevent version divergence.
2. **Lingering Port Teardown**:
   - Computes a unique, branch-mapped TCP port for each implementation based on the SHA256 of its branch name.
   - Terminates any lingering processes (like local MQTT brokers or database servers) active on those ports to avoid execution collisions.
3. **Automated Agent Update (`@UPDATE.md`)**:
   - Invokes the Gemini developer agent inside each implementation workspace using the `@UPDATE.md` play script.
   - The agent automatically merges `main` into the active branch, audits the code against specifications under `spec/`, applies necessary code refactoring, and executes local smoke tests to verify correctness.
   - Detailed logs are outputted to `impl/<ID>.log`.
4. **Git Verification & Push**:
   - Performs a git status check on each sibling directory.
   - If there are unpushed commits on the sibling branch, automatically pushes them to the remote tracking branch (e.g. `origin/impl_<ID>`), keeping the active branches fully aligned.

---

## 3. Phase 2: Live Cross-Implementation Interoperability Testing (`tools/run_cross`)

The second phase executes the live cross-implementation test matrix to verify that the different implementations can seamlessly interoperate.

### Command and Usage:
```bash
./tools/run_cross [options]
```

### Key Options:
- `-p` or `--parallel`: Runs integration tracks in parallel across implementations.
- `-s` or `--sandbox`: Runs under sandbox-isolated conditions.

### Operational Sequence:
1. **Clean Workspace**:
   - Prepares output directories (`out/` and `impl/`) and purges stale logs or metrics.
2. **Matrix Generation ($N \times N$)**:
   - For each integration track (e.g., track `A`), allocates isolated ports using SHA256 mapping via the shared `tools/port_utils` utility.
   - Automatically bootstraps the local environment by calling the implementation's setup utility with the `--offline` flag to ensure safe, hermetic execution inside test sandboxes without network-related pip latency.
   - Launches a background message observer to capture network traces.
   - Runs a per-pairing verifier (validator) with an isolated log file (`out/${v}_${b}_verifier.log`) to prevent log accumulation across pairings.
   - Sequentially runs every *other* implementation as the active orchestrator (`butler`) and starts the simulated Pubber device under test (DUT).
   - Simulates a managed firmware update via `site_trigger` and polls for terminal state transitions.
   - The matrix includes self-validation pairs (e.g., `impl_A verifies impl_A`) where the verifier and butler are the same implementation.
   - **Secure Connection Specification:** The cross-test harness uses `mqtts://rocket:monkey@localhost:${port}/` as the connection specification for all track components, providing a fixed principal for certificate-based TLS authentication across all implementations.
   - **Certificate Sharing:** The harness dynamically symlinks the target butler's certificate directory (`impl/{b}/impl/udmi/var/mosquitto/certs`) to the active verifier's broker certificate folder to ensure cross-implementation certificate trust.
3. **Teardown**:
   - After each track completes, the harness calls `bin/setup --stop` to perform hermetic PID-based teardown of local background services (etcd, mosquitto) and then cleans all three branch-mapped ports (MQTT, etcd, etcd peer) using the shared `cleanup_port()` function from `tools/port_utils`.
4. **Evidence Collection**:
   - Captures and saves full execution traces to `impl/<verifier_id>/logs/<verifier_id>_validates_<butler_id>.log` and copies the trace to the respective butler directory.
   - Analyzes logs to determine if state transitions (`pending -> success`) completed successfully, using exact spec-compliant log patterns from `spec/butler.md` Section 9.2.
   - Generates the authoritative integration test report in `impl/test_summary.txt` listing the exact outcomes (`PASS` or `FAIL`) for every pairing.
   - Records detailed timing and execution metrics in `out/performance_analysis.txt`.

---

## 4. Phase 3: Specification Merge and Refinement (`tools/run_merger`)

The final phase performs a purely static, offline analysis of the logs and test reports generated during the cross run to reconcile spec-compliance issues and evolve the specifications.

### Command and Usage:
```bash
./tools/run_merger [options]
```

### Key Options:
- `-s` or `--sandbox`: Runs the merge agent workflow under sandbox-isolated conditions.

### Operational Sequence:
1. **Execute Merger Workflow (`@MERGER.md`)**:
   - Invokes the Gemini merger agent on the parent workspace using the instructions in `MERGER.md`.
2. **Static Analysis of Artifacts**:
   - The agent reads `impl/test_summary.txt` to identify failing pairs and inspects the compiled logs in `out/` and `impl/logs/` to understand why they failed.
   - Analyzes recent git changes/diffs across implementation folders and any `spec_analysis.md` feedback files written by implementation agents.
3. **Spec and Instruction Refinement**:
   - **Upstream Fixes**: If failures were due to defects in the immutable third-party `impl/udmi` tools, the agent documents them clearly in a `uufi_analysis.md` file at the workspace root, as dictated by the "Empirical Defect and Impossible Constraint Policy".
   - **Local Spec Refinement**: Resolves local Butler spec ambiguities or contradictions by editing specifications in the `spec/` directory directly (e.g., `spec/butler.md`).
   - **Instruction updates**: Refines the rules inside `UPDATE.md` to prevent future implementation divergence.
4. **Clean Checkout**:
   - All proposed specification and guideline updates are left staged or unstaged in the active `main` branch, keeping the repository clean and ready for human inspection.

---

## 5. Developer Commands Reference Sheet

| Execution Command | Scope | Purpose & Actions | Primary Outputs & Side Effects |
| :--- | :--- | :--- | :--- |
| **`./tools/run_updates`** | Sibling directories (`impl/*`) | Clones/checks out sibling branches, pins UDMI targets, and runs `@UPDATE.md` agent refactoring and smoke tests. Exits non-zero if any implementation fails. | Pushes updated, verified code to sibling branches (`origin/impl_<ID>`). Logs saved to `impl/<ID>.log`. |
| **`./tools/run_cross`** | Cross-testing matrix | Executes live $N \times N$ testing, running each implementation as a verifier against every implementation as a butler. Uses per-pairing verifier logs and event-driven polling. | Generates `impl/test_summary.txt`, trace files in `impl/<ID>/logs/`, and `out/performance_analysis.txt`. |
| **`./tools/run_merger`** | Parent workspace (`main`) | Executes the `@MERGER.md` static analysis agent to parse test results, refine specs, and generate upstream analysis. | Updates files under `spec/`, `UPDATE.md`, and creates/updates `uufi_analysis.md` on `main`. |

---

## 6. Feedback File Reference

The workflow uses three feedback files for inter-agent communication. Each has a specific writer, reader, and lifecycle:

| File | Location | Writer | Reader | Purpose | Lifecycle |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`spec_analysis.md`** | Implementation root (`impl/<ID>/spec_analysis.md`) | Implementation Agent (`UPDATE.md`) | Merger Agent (`MERGER.md`) | Documents spec ambiguities, contradictions, or external incompatibilities found during implementation. | Created/updated when issues found; **removed** when spec is clear and correct. |
| **`merge_analysis.md`** | Implementation root (`impl/<ID>/merge_analysis.md`) | Merger Agent (`MERGER.md`) | Implementation Agent (`UPDATE.md`) | Documents implementation-specific failures and non-compliance found during cross-testing. | Created when failures found; **removed** by Implementation Agent once resolved. |
| **`uufi_analysis.md`** | Parent workspace root (`uufi_analysis.md`) | Merger Agent (`MERGER.md`) | Human / Upstream | Documents upstream UUFI spec defects in the immutable `impl/udmi` that cannot be fixed locally. | Created/updated when upstream defects found; persists for human review. |

---

## 7. Port Allocation Scheme

All workflow scripts use the shared `tools/port_utils` utility for consistent branch-mapped port allocation and cleanup. Each implementation gets three ports:

| Port | Formula | Purpose |
| :--- | :--- | :--- |
| MQTT port | `45000 + (SHA256(branch_name) % 3000)` | MQTT broker |
| etcd port | MQTT port + 1 | etcd client |
| etcd peer port | MQTT port + 1001 | etcd peer communication |

Both `run_updates` and `run_cross` clean up all three ports to prevent lingering processes between runs. `run_updates` uses the shared `cleanup_branch_ports()` function, while `run_cross` calls `cleanup_port()` for each of the three ports individually (as part of its per-track teardown sequence).
