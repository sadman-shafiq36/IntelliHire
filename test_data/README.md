# test_data/

Synthetic CVs used by the automated tests and for edge-case work. These **are** committed,
because they describe people who do not exist (BID §8.5 permits trainee-generated synthetic
CVs, and N13 only forbids real CVs belonging to real individuals).

The 20 CVs supplied by the programme live in `sample_data/cvs/` instead, which is git-ignored.

Planned coverage, to be added with WBS Module 5 (CV parsing):

| Case | Why it matters |
|---|---|
| Standard single-column PDF | The happy path |
| Two-column layout | Naive text extraction interleaves the columns |
| Scanned / image-only PDF | Must trigger the OCR fallback |
| DOCX | The second required input format |
| Missing education section | Extraction must not assume every section exists |
| Corrupt / zero-byte file | Must be flagged and skipped, never abort the batch |
