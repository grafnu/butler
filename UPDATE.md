DO NOT ASK FOR CLARIFICATION OR DIRECTION. YOU ARE AN AUTONONOMOUS AGENT WITHOUT
THE ABILITY TO ASK QUESTIONS OR SEEK ADVICE. YOU SHOULD ALWAYS PROCEED AND OPEN
THE PR.

To update a build:

This should NOT be performed on the `main` or `gemerger` branch.
This sequence is for implementation branches only.
Only execute this if the `butler` directory already exists (and has code in it).

* `git fetch origin`
* `git merge origin/main`
* Update the code to comply with the specifications in `spec/`
* `git push`

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
Do not ask for clarification on any missing elements. If necessary,
add a "ASSUMED:" tag in the spec file where it was ambigous and you
needed to make a guess at the right thing to do.
Your job is to figure everything out yourself and then generate a commit
or PR that encapsulates the end result. Don't ask for permission or
if anything is acceptable.

If necessary, instead of asking what to do, follow the instructions in `REBUILD.md`
to do a clean build from scratch.
