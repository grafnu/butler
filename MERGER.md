DO NOT ASK FOR CLARIFICATION OR DIRECTION. YOU ARE AN AUTONONOMOUS AGENT WITHOUT
THE ABILITY TO ASK QUESTIONS OR SEEK ADVICE.

# Instructions on how to run a merge integration test between multiple implementations.

Only changes to the `spec/` files and `test_summary.txt` should be committed and pushed.
No other files should have a diff or be commited to the PR.

The primary goal is to empirically ensure that the provided specs are robust and coherent
by interoperability testing between multiple different implementations of the `butler` spec.
If there are failures or significant inconsistencies update the specs in `spec/` accordingly.
Spec compliance is defined as successfully passing the `smokeit` test across all cross-implementation
pairs with zero verification failures and consistent behavioral logs.
If everything is spec compliant then there is nothing to do except report success.

Create/update the file `test_summary.txt` with PASS/FAIL results of the testing
results in the form (e.g.) `impl_A verifies impl_B: PASS` sorted in
lexagraphical order (e.g. using `sort` on the file).

If an implementations need to be fixed to achieve a PASS then the spec MUST be updated
to reflect the change. This should either be as a clarification
to remove ambiguity, or a reminder to reinforce some particularly senstive
point of the spec.

First merge `origin/main` into this branch to make sure all specs and
other details are up to date.

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

Run the setup and tests multiple times, once for each impl N as
`butler` connecting to N+1 as the `verifier`. If there are N
implementations then there should be exactly N test runs,
determinstically ordered.
