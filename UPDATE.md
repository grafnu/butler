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
   - This procedure MUST NOT be performed on the `main` or `gemerger` branch, or any other unrecognized branch.
   - Run `git branch --show-current` to verify. If the branch is invalid, halt the process.
   - Ensure the working directory is clean using `git status` before beginning.

2. **Upstream Synchronization:**
   - Fetch the latest changes from the remote:
     ```bash
     git fetch origin
     ```
   - Merge `origin/main` into the current branch to pull in any specification updates:
     ```bash
     git merge origin/main
     ```
   - *Conflict Resolution:* If merge conflicts occur within files in `spec/`, since they are immutable, the agent MUST always auto-resolve them in favor of `origin/main` without exception, even if it breaks the current branch's historical custom tests, as specs on `main` are the immutable source of truth.

---

## Code Compliance and Spec Auditing

To ensure the codebase complies with any new or modified specifications from `spec/`:

1. **Audit Spec Changes:**
   - Identify which specification files changed during the merge:
     ```bash
     git diff ORIG_HEAD HEAD -- spec/
     ```
   - Carefully review changes in `spec/butler.md`, `spec/blobstore.md`, and `spec/update.md`.

2. **Update Implementation Logic:**
   - Symmetrically update implementation code in `butler/` and command wrappers in `bin/` to satisfy all newly introduced requirements or compliance formats.

---

## Verification & Testing Workflow

Before finishing, the implementation MUST be verified to prevent regressions:

1. **Environment Setup:**
   - Run the local setup utility to ensure the MQTT broker or other local infrastructure is running and the connection specifications are correct:
     ```bash
     bin/setup
     ```
   - **Shared Port Prohibition:** To allow simultaneous test runs on the same machine, sharing default MQTT ports (e.g., `1883`, `8883`) or etcd ports is strictly prohibited. The system MUST dynamically allocate or negotiate isolated, non-default ports for the local MQTT broker and etcd instances (such as a random high port or an offset derived from the workspace/branch) to completely prevent cross-instance interference.
   - *No Diagnosis of UDMIS Errors:* The system MUST NOT attempt to diagnose or fix any potential errors with UDMIS. Specifically, when starting the broker or DUT, it should either work as specified or report an error. The system should either start up and work as intended, or fail with a clear error indicating that the UDMIS specification or system is broken.
   - *Blobstore Provider Configuration:* During this automated update flow, the system must default to a local provider. It is acceptable to specify a non-local `BUTLER_BLOBSTORE_PROVIDER` (such as `gcs`) ONLY if it is explicitly configured via environment variables and all necessary authentication and cloud resources are already set up.

2. **Execute Code Quality & Unit Tests:**
   - Prior to running integration smoke tests, the update flow MUST execute any configured static code analysis tools, linters (e.g., `pylint`, `black`, `flake8`), or standard unit tests (e.g., `pytest`) if they are configured in the repository to verify code health.

3. **Execute Smoke Tests:**
   - Run the automated, non-interactive integration smoke tests to verify the complete registration, update, and failure rollback logic:
     ```bash
     bin/smokeit
     ```
   - *Handling Failures:* If tests fail, diagnose using log outputs, apply necessary bug fixes strictly within the `butler/` and `bin/` directories, and re-run until all tests pass.
   - *Test Outputs and Logs:* Verifier and smoke-test outputs (e.g., test logs or summary files) SHOULD be saved locally in an ignored directory (such as `testing/` or `tmp/`) for debugging, but they MUST NOT be committed to the repository or uploaded as part of the PR/commit artifact.

---

## File and Workspace Integrity Audit

1. **Strict File Restrictions:**
   - At the end of the run, the *only* changes in the repository should be to files in the `butler/` and `bin/` directories (and changes to `UPDATE.md` itself when explicitly directed).
   - There should NOT be any other stray files or changes made to any other files (e.g., `spec/`, `.gitignore`, `AGENTS.md`, `WORKFLOW.md`, etc.).
   - It is acceptable to have temporary files in directories ignored by `.gitignore` (such as `testing/`), but the `.gitignore` file itself MUST NOT be modified. Any generated verifier, smoke test logs, or summary files MUST NOT be committed or tracked.

2. **Commit and Push Procedure:**
   - Stage and commit only the allowed changed files (do not use `git add .` or `git add -A` unprompted).
   - If no functional code changes were required to comply with the specs, create an empty commit to signal the successful run:
     ```bash
     git commit --allow-empty -m "Update process made no changes"
     ```
   - If changes were made, propose a clear, concise commit message explaining the updates. If any spec-driven ambiguities were encountered and resolved by assumption, include an `"ASSUMPTION:"` comment in the commit message to explain.
   - Push the committed branch:
     ```bash
     git push
     ```
---

## Testing and Spec improvements

1. **Iterative Suggestions**
   - Display any suggestions for this document that would make future testing runs easier or more consistent.
   - Display any suggestions for the Butler spec that would improve testability.
   - Display any suggestions on improvements to the overal workflow to increase speed or reliability.

