DO NOT ASK FOR CLARIFICATION OR DIRECTION. YOU ARE AN AUTONONOMOUS
AGENT WITHOUT THE ABILITY TO ASK QUESTIONS OR SEEK ADVICE.

# Instructions on how to run a merge integration test between multiple implementations.

Only changes to the `spec/` files should be
committed and pushed from this repo.
No other files should have a diff or be
commited to the PR. If there are no changes to the spec,
then an empty commit should be pushed with the log message
"Clean run with no spec or result changes."

The primary goal is to empirically ensure that the provided specs are
robust, coherent, and comply to the spec,
by interoperability testing between multiple
different implementations of the `butler` spec.  If there are failures
or significant inconsistencies update the specs in `spec/`
accordingly.  Spec compliance is defined as successfully passing the
`smokeit` test across all cross-implementation pairs with zero
verification failures and consistent behavioral logs.  If everything
is spec compliant then there is nothing to do except report success.

The goal is to be spec compliant, not to just pass the tests. The
spec should be concise and authoritave. It should focus on the specific
requirements, avoiding "should" and "may", focusing on what is
strictly required, what the system must do to function and be compliant.
Either something is done correctly, or it doesn't matter how it's done.

The output of the skill MUST be an updated spec (`.md`) file (e.g., under `spec/` such as `spec/butler.md`). Simply patching or updating the local implementation code in `impl/` is NOT sufficient, as changes there will not be saved or committed. Any temporary code adjustments, patches, or test workarounds that are required to bring implementations into compliance or to let the integration tests pass MUST be codified as strict, explicit requirements in the formal specification files under `spec/`.

First merge `origin/main` into this branch to make sure all specs and
other details are up to date.

There are (should be) multiple working subdirectories, each one
containing a different version of an implementation. Check the remote
branches of the form `impl_ID`. Each one is a different implementation
of the butler spec.

**Cloning and Syncing Branches & Parallel Fetch/Audits:**
To prepare the sibling implementations for testing, list and clone/sync them. To minimize tool and test initialization latency, the upstream synchronization (fetching/cloning) and sibling directory structure audits MUST be executed in parallel (concurrently) across all discovered implementation branches rather than sequentially. This requires:
- Retrieving the list of remote implementation branches (branches prefixed with `impl_`).
- Concurrently cloning new implementation repositories or fetching updates and performing a hard reset on existing directories in the background.
- Concurrently verifying the presence of required file resources (such as `AGENTS.md`) across all synchronized implementation workspaces, failing immediately if the workspace structure is non-compliant.

**Setting up Independent Virtual Environments:**
Each implementation must run within its own isolated Python virtual environment based on its specific `requirements.txt`. Establish, activate, and satisfy all requirements using the Python packages installer prior to setting up the testing environments or executing test suites.

Execute the functional equivalent of the `smokeit` integration tests using the appropriate tools from different subdirectories. Two versions are tested at a time, one primarily for `verifier` the other for `butler`. Both implementations should use the same connectivity specification (as defined in `AGENTS.md`) to ensure they can communicate.

Run and capture the output/logs of both the `butler` and `verifier` processes to separate log files (`impl/{ID}.log`).

**Specification Audit and Update Guidelines:**
The specification MUST be strict, authoritative, clear, and unambiguous. It must NOT be relaxed or updated just to accommodate non-compliant behaviors of buggy test frameworks or sub-implementations. If a test harness (like a cross-testing monitor) or any system implementation is found to use a non-compliant or deviant format (such as nested payload wrappers or custom version fields), the test harness or implementation itself MUST be failed and corrected to comply with the authoritative specification, rather than modifying the specification to allow non-standard alternatives.

Ensure that the specifications clearly define strict, unique, and unambiguous requirements for the following critical interoperability areas:
1.  **Handshake Payload Formatting:** Mandate exactly ONE standard flattened format for Handshake Step 1 and Step 2 request/reply blocks, where the `"setup"` and `"reply"` payload blocks reside directly at the payload root (no nested `"udmi"` wrappers).
2.  **Model Updates Structure:** Mandate exactly ONE standard format for cloud model update payloads, where the `"registries"` key resides directly at the payload root (no nested `"cloud"` wrappers).
3.  **Expected Version Configuration:** Specify exactly ONE standard way to define the expected version: under the standard software dictionary structure within the device's system configuration (`system.software.<subsystem>`). Any alternative properties like `"target_version"` are strictly prohibited and MUST NOT be accepted.
4.  **Topic Suffix Formatting:** Clarify that all UUFI topic paths MUST include both a subtype and a subfolder segment (`/c/{subtype}/{subfolder}`), and topic generation utilities must not generate truncated or omitted suffixes.
5.  **Subsystem Reporting Structure:** Ensure the actual state reporting matches standard UDMI schemas, wrapping the subsystem ID (specifically `"system"`) within the `"blobs"` dictionary under `"blobset"`.
6.  **Envelope and Configuration Attributes:** Specify compliance rules for envelope attributes like `"nonce"` deduplication/graceful-processing, and config attributes like `"version"`.

If the smoke test fails, indicating that there is an incompatibility
in the implementations, diagnose and analyze to determine what the
problem is, and update the specs (in `spec/`) to
remediate.

If the smoke test passes, analyze the generated log files to see if
there is any other discrepancy or ambiguities that should be addressed
and likewise update `spec/`.

The actual implementation in `*/butler/` will be different and that's
expected. (Same with `*/bin/`).

Any test files should be generated in directories covered by
`.gitignore`. Do not clean up the test runs after execution. At the
end of the testing, there should not be any artifacts left that are
visible by `git status`.

Run the setup and tests multiple times, once for each impl as
`butler` with another impl as `verifier`. If there are N
implementations then there should be exactly N*(N-1) test runs.
Every combination of `butler` & `verifier` should be tested.

**Dynamic Port Allocation for Parallel Runs:**
To prevent parallel tests from conflicting and causing socket bind/port collisions, you MUST dynamically allocate a unique free port for each parallel broker instance. Implementations MUST programmatically obtain a free, unoccupied local TCP port from the OS at runtime, and pass this unique port configuration to all setup and verification runners.

Run the tests all in parallel at the same time, using different
prefixes to disambiguate the working sets. All trial runs should
take about the same amount of time to complete, so if some runs
are taking more than twice as long as the passing runs, then it
should be aborted and considered a failure.

Create/update a file `impl/test_summary.txt` with PASS/FAIL/FIXED results
of the testing results in the form (e.g.) `impl_A verifies impl_B:
PASS` sorted in lexicographical order (e.g. using `sort` on the
file). PASS means it passed unmodified, FAIL means it failed and could
not easily be fixed, and FIXED means that it passed after
modifications.

If an implementations need to be fixed in order to be spec compliant,
then the result should be FIXED and the spec updated accordingly.
This should either be as a clarification to remove ambiguity, or a
reminder to reinforce some particularly senstive point of the spec.
If possible, for each impl branch, the system should pull from upstream,
merge with the local fixes, and then push a commit upstream to the
remote origin for that impl_ branch.  This is not a strict requirement
so if there is a merge issue that can't be easily resolved the push is not required.
