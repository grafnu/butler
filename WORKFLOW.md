# Multi-Implementation Development and Verification Workflow

This document provides a comprehensive guide to the multi-implementation workflow of the **Butler Managed Update System**. This process is designed to synchronize, test, and merge specifications and implementations across multiple sibling repositories and git branches.

AGENTS: When executing this workflow in a non-interactive capacity, capture observations to a file `workflow_analysis.md` for future processing.

---

## 1. Multi-Implementation Architecture

To ensure robustness and compliance with the core architectural specifications, the Butler project maintains multiple independent implementations of the system (typically designated as implementations `A`, `B`, `C`, and `D` located in `impl/A`, `impl/B`, `impl/C`, and `impl/D` respectively). These correspond to the git branches `impl_A`, `impl_B`, `impl_C`, and `impl_D`.

The integration pipeline maintains strict consistency using a three-phase sequence driven by automated helper scripts:
```
           [bin/run_updates]  ===>  [bin/run_cross]  ===>  [bin/run_merger]
```

## 2. Phase 1: Implementation Synchronization and Updates (`bin/run_updates`)

The first phase ensures that all implementation branches are fully updated, checked out, and synchronized with the core specification changes on the parent `main` branch.

### Command and Usage:
```bash
./bin/run_updates [options]
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

## 3. Phase 2: Live Cross-Implementation Interoperability Testing (`bin/run_cross`)

The second phase executes the live cross-implementation test matrix to verify that the different implementations can seamlessly interoperate.

### Command and Usage:
```bash
./bin/run_cross [options]
```

### Key Options:
- `-p` or `--parallel`: Runs integration tracks in parallel across implementations.
- `-s` or `--sandbox`: Runs under sandbox-isolated conditions.

### Operational Sequence:
1. **Clean Workspace**:
   - Prepares output directories (`out/` and `impl/`) and purges stale logs or metrics.
2. **Matrix Generation ($N \times (N - 1)$)**:
   - For each integration track (e.g., track `A`), allocates isolated ports using SHA256 mapping.
   - Automatically bootstraps the local environment by calling the implementation's setup utility.
   - Launches a background message observer to capture network traces.
   - Runs a background validator/verifier utilizing the implementation's own `verifier` executable.
   - Sequentially runs every *other* implementation as the active orchestrator (`butler`) and starts the simulated Pubber device under test (DUT).
   - Simulates a managed firmware update via `site_trigger` and waits for state transitions to complete.
3. **Evidence Collection**:
   - Captures and saves full execution traces to `impl/<verifier_id>/logs/<verifier_id>_validates_<butler_id>.log` and copies the trace to the respective butler directory.
   - Analyzes logs to determine if state transitions (`pending -> success`) completed successfully.
   - Generates the authoritative integration test report in `impl/test_summary.txt` listing the exact outcomes (`PASS` or `FAIL`) for every pairing.
   - Records detailed timing and execution metrics in `out/performance_analysis.txt`.

---

## 4. Phase 3: Specification Merge and Refinement (`bin/run_merger`)

The final phase performs a purely static, offline analysis of the logs and test reports generated during the cross run to reconcile spec-compliance issues and evolve the specifications.

### Command and Usage:
```bash
./bin/run_merger [options]
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
| **`./bin/run_updates`** | Sibling directories (`impl/*`) | Clones/checks out sibling branches, pins UDMI targets, and runs `@UPDATE.md` agent refactoring and smoke tests. | Pushes updated, verified code to sibling branches (`origin/impl_<ID>`). Logs saved to `impl/<ID>.log`. |
| **`./bin/run_cross`** | Cross-testing matrix | Executes live $N \times (N - 1)$ testing, running each implementation as a verifier against every other implementation as a butler. | Generates `impl/test_summary.txt`, trace files in `impl/<ID>/logs/`, and `out/performance_analysis.txt`. |
| **`./bin/run_merger`** | Parent workspace (`main`) | Executes the `@MERGER.md` static analysis agent to parse test results, refine specs, and generate upstream analysis. | Updates files under `spec/`, `UPDATE.md`, and creates/updates `uufi_analysis.md` on `main`. |
