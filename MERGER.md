DO NOT ASK FOR CLARIFICATION. YOU ARE AN AUTONONOMOUS AGENT WITHOUT
THE ABILITY TO ASK QUESTIONS OR SEEK ADVICE.

# Instructions on how to run a merge integration test between two implementations.

The goal is to test multiple different implementations of the `butler`
spec, as defined in `spec/`, and if there are failure or significant
inconsistencies update the specs in `spec/` accordingly. Spec compliance
is defined as successfully passing the `smokeit` test across all cross-implementation
pairs with zero verification failures and consistent behavioral logs. If
everything is spec compliant then there is nothing to do except report
success. It is NOT necessary to fix the implementations (except as a
means to figure out what the correct `spec/` would be). Create/update
the top-level file `impl_test_summary.txt` with the results of the testing
results in the form (e.g.) `impl_A verifies impl_B: PASS`.

There are (should be) multiple working subdirectories, each one
containing a different version of an implementation.  Check the remote
branches of the form `impl_ID`. Each one is a different implementation
of the butler spec. Clone each one into the `impl/ID` directory
(replacing the `_` with the file separator `/`). If they already
exist, make sure they are up to date with the remote origin (`git
pull`).

The `venv` based off of `requirements.txt` needs to be setup for each
subdirectory independently.

Execute the functional equivalent of `smokeit` (found in
`impl/{ID}/bin/smokeit`) except use the appropriate tools from
different subdirectories. Two versions are tested at a time, one
primarily for `verifier` the other for `butler`. Both implementations
should use the **same** connectivity specification (as defined in
`AGENTS.md`) to ensure they can communicate.

Run `observe` (found in `impl/{ID}/bin/observe`) twice, once from each
implementation, and capture the output to separate log files
`impl/{ID}.log`.

If the smoke test fails, indicating that there is an incompatibility
in the implementations, diagnose and analyze to determine what the
problem is, and recommend a change to the specs (in `spec/`) to
remediate.

If the smoke test passes, analyze the generated log files to see if
there is any other discrepancy or ambiguities that should be addressed
and likewise recommend changes to `spec/`.

The actual implementation in `*/butler/` will be different and that's
expected. (Same with `*/bin/`).

Any test files should be generated in directories covered by
`.gitignore`. Do not clean up the test runs after execution. At the
end of the testing, there should not be any artifacts left that are
visible by `git status`.

Run the setup and tests multiple times, exactly just enough iterations
to satisfy the following constraints: * Every branch (ID) is run at
least twice, once as `butler` and the other as `observe`.  * The graph
of connected components should be a connected graph (e.g., if you have
A, B, and C, you might test A-B and B-C).

Only changes to the `spec/` files and `impl_test_summary.txt` should be committed and pushed.

Do not ask any questions or seek clarification. Your goal is to simply
test, recommand changes, and report results.
