To update a build:

This should NOT be performed on the `main` or `gemerger` branch.
This sequence is for implementation branches only.
Only execute this if the `butler` directory already exists (and has code in it).

* `git fetch origin`
* `git merge origin/main`
* Update the code with any recent changes in `spec/`
* `git push`
