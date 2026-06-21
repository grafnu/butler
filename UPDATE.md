# Update Procedure for Butler Implementation Builds

DO NOT ASK FOR CLARIFICATION OR DIRECTION. YOU ARE AN AUTONOMOUS AGENT WITHOUT
THE ABILITY TO ASK QUESTIONS OR SEEK ADVICE. YOU SHOULD ALWAYS PROCEED AND OPEN
THE PR.

This document outlines the standard procedure for pulling in specifications from
`main` and updating implementation builds to ensure full compliance and stability.

---

## Pre-Requisites & Validation

1. **Branch Verification:**
   - This procedure MUST only be applied to branches that start with `impl_` (e.g., `impl_B`).
   - This procedure MUST NOT be performed on the `main` or `merger` branch, or any other unrecognized branch.
   - Verify that the active git branch name is valid and the working directory is clean before beginning.

2. **Upstream Synchronization:**
   - Fetch the latest changes from the remote repository.
   - Merge `origin/main` into the current branch to pull in any specification updates.
   - *Conflict Resolution:* If merge conflicts occur within files in `spec/`, since they are immutable, the agent MUST always auto-resolve them in favor of `origin/main` without exception, even if it breaks the current branch's historical custom tests, as specs on `main` are the immutable source of truth.

3. **Automatic Port Status Check:**
   - Prior to launching any local brokers or executing tests, the update process MUST run a dynamic pre-check to detect if there are any active processes currently listening on the branch-mapped MQTT/testing port (such as `40000-49999`) or standard etcd/MQTT ports.
   - This check can be performed using standard utilities (such as `ss -lntp`, `netstat`, or `lsof`) or programmatic socket connection attempts.
   - If an active broker or process is detected on the target port outside of the test runner's orchestration, the runner MUST list the active process info and PID (if accessible) to standard error to assist in diagnosing and debugging rogue or manually started brokers before proceeding.

---

## Code Compliance and Spec Auditing

To ensure the codebase complies with any new or modified specifications from `spec/`:

1. **Audit Spec Changes:**
   - Identify which specification files changed during the merge by running a differences check on the `spec/` directory.
   - Carefully review changes in `spec/butler.md`, `spec/blobstore.md`, and `spec/update.md`.

2. **Update Implementation Logic:**
   - Symmetrically update implementation code in `butler/` and command wrappers in `bin/` to satisfy all newly introduced requirements or compliance formats.

---

## Verification & Testing Workflow

Before finishing, the implementation MUST be verified to prevent regressions:

1. **Environment Setup:**
   - **Environment Validation:** Prior to setting up the environment, verify that Python virtual environment and dependencies are satisfied according to the authoritative validation steps specified in `spec/butler.md` Section 10.1.1.
   - Run the local setup utility to ensure the MQTT broker or other local infrastructure is running and the connection specifications are correct.
   - **Shared Port Prohibition:** To allow simultaneous test runs on the same machine, sharing default MQTT ports (e.g., `1883`, `8883`) or etcd ports is strictly prohibited. The system MUST dynamically allocate or negotiate isolated, non-default ports for the local MQTT broker and etcd instances as specified by the SHA256 branch hashing port mapping formula in `spec/butler.md` Section 10.
   - *No Diagnosis of UDMIS Errors:* The system MUST NOT attempt to diagnose or fix any potential errors with UDMIS. Specifically, when starting the broker or DUT, it should either work as specified or report an error. The system should either start up and work as intended, or fail with a clear error indicating that the UDMIS specification or system is broken.
   - *Blobstore Provider Configuration:* During this automated update flow, the system must default to a local provider. It is acceptable to specify a non-local `BUTLER_BLOBSTORE_PROVIDER` (such as `gcs`) ONLY if it is explicitly configured via environment variables and all necessary authentication and cloud resources are already set up.

2. **Execute Code Quality & Unit Tests:**
   - Prior to running integration smoke tests, the update flow MUST execute any configured static code analysis tools, linters, or standard unit tests if they are configured in the repository to verify code health.

3. **Execute Smoke Tests:**
   - Run the automated, non-interactive integration smoke tests (`bin/smokeit`) to verify the complete registration, update, and failure rollback logic.
   - *Handling Failures:* If tests fail, diagnose using log outputs, apply necessary bug fixes strictly within the `butler/` and `bin/` directories, and re-run until all tests pass.
   - *Test Outputs and Logs:* Verifier and smoke-test outputs (e.g., test logs or summary files) SHOULD be saved locally in an ignored directory (such as `testing/` or `tmp/`) for debugging, but they MUST NOT be committed to the repository or uploaded as part of the PR/commit artifact.

---

## File and Workspace Integrity Audit

1. **Strict File Restrictions:**
   - At the end of the run, the *only* changes in the repository should be to files in the `butler/` and `bin/` directories (and changes to `UPDATE.md` itself when explicitly directed).
   - There should NOT be any other stray files or changes made to any other files (e.g., `spec/`, `.gitignore`, `AGENTS.md`, `WORKFLOW.md`, etc.).
   - It is acceptable to have temporary files in directories ignored by `.gitignore` (such as `testing/`), but the `.gitignore` file itself MUST NOT be modified. Any generated verifier, smoke test logs, or summary files MUST NOT be committed or tracked.

2. **Commit and Push Procedure:**
   - Stage and commit only the allowed changed files (do not use global or untargeted add commands unprompted).
   - If no functional code changes were required to comply with the specs, create an empty commit with a simple "No changes required" message,
     with no other text, in the git commit log to signal the successful run.
   - If changes were made, propose a clear, concise commit message explaining the updates. The system should only summarize *changes* that were actually made and should not describe what tests or checks were performed.
   - Push the committed branch to the remote repository.

---

## Testing Encountered Issues

1. **Actually Encountered Issues**
   - **Encountered Issues List:** When actually testing and verifying the implementation under this procedure, the agent or runner MUST dynamically list and output only the specific operational issues, errors, or bugs that were actually encountered due to a bad specification and resolved *during that specific run* of `UPDATE.md`. It MUST NOT report any theoretical or static-analysis-based issues, nor any issues that were statically pre-instructed or requested to change, ensuring the documented issues are strictly empirical runtime findings from that active execution. It should NOT report any errors from a bad _implementation_, rather only errors resulting from a bad _specification_ (input `.md` file).
2. **Avoid Hacky Workarounds**
  - **Should not hack:** If there is a problem identified with UDMI or UUFI that is in conflict with the Butler spec the system should NOT work around the problem in volation of the spec. Rather it should just report the error and fail. The system should report and hard fail any fundamental specification incompatibilities rather than trying to work around them.
