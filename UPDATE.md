DO NOT ASK FOR CLARIFICATION OR DIRECTION. YOU ARE AN AUTONONOMOUS AGENT WITHOUT
THE ABILITY TO ASK QUESTIONS OR SEEK ADVICE. YOU SHOULD ALWAYS PROCEED AND OPEN
THE PR.

To update a build:

This should NOT be performed on the `main` or `gemerger` branch.
This sequence is for implementation branches only.
Only execute this if the `butler` directory already exists (and has code in it).

* `git fetch origin`
* `git merge origin/main`
* Update the code with any recent changes in `spec/`
* `git push`

Do not ask how to complete the task, and do not check if you should proceed.
Do not ask for clarification on any missing elements. If necessary,
add a "ASSUMED:" tag in the spec file where it was ambigous and you
needed to make a guess at the right thing to do.
Your job is to figure everything out yourself and then generate a commit
or PR that encapsulates the end result. Don't ask for permission or
if anything is acceptable.

If necessary, instead of asking what to do, follow the instructions in `REBUILD.md`
to do a clean build from scratch.
