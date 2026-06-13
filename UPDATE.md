DO NOT ASK FOR CLARIFICATION OR DIRECTION. YOU ARE AN AUTONOMOUS AGENT WITHOUT
THE ABILITY TO ASK QUESTIONS OR SEEK ADVICE. YOU SHOULD ALWAYS PROCEED AND OPEN
THE PR.

To update a build:

This should only be applied to branches that start with `impl_`.
This should NOT be performed on the `main` or `gemerger` branch, or any other
unrecognized branch. This sequence is for implementation branches only.

* `git fetch origin`
* `git merge origin/main`
* Update the code to comply with the specifications in `spec/`
* `git push`

Ensure that the all the files listed in AGENTS.md that indicate
they should not be removed or altered are the same as the `main` branch.
Ensure there are no files that are not allowed as per AGENTS.md.

The files in `spec/` should be considered immutable and not changed. All files
not explicitly indicated to be functional work zones should be immutable.

At the end of the run, the _only_ changes should be to code in the `butler/` and
`bin/` directories. There should NOT be any other stray files or changes made.
It is OK to have files that are ignored by `.gitignore` (but it is NOT OK to change
`.gitignore` itself). If there are no code changes made then there should be an
"empty" commit that just has a log message that says "Update process made no changes".

Do NOT check out any other branch implementations, assume that they do not exist
or are off limits.

Do not ask how to complete the task, and do not check if you should proceed.
Do not ask for clarification on any missing elements.
Your job is to figure everything out yourself and then generate a commit
or PR that encapsulates the end result. Don't ask for permission or
if anything is acceptable. If necessary, put an "ASSUMPTION:" comment
into the commit message to explain spec-driven confusion.
