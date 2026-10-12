# sample_data/

Holds the **20 sample CVs supplied by the programme** (BID §8.5).

`sample_data/cvs/` is git-ignored on purpose. BID requirement **N14** forbids committing
candidate personal data, and **§8.5** states the provided CVs must not reach a public
repository. Copy the provided files into `sample_data/cvs/` on your own machine; they will
never be staged.

Synthetic CVs used by the automated tests live in `test_data/cvs/` instead, and *are*
committed, because they describe people who do not exist.
