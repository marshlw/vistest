# Contributors

VisTest was designed and written by:

- **Kirill Kulagin** — original author, engine, service, UI, everything through
  the first public release.

---

## Contributing

Contributions are welcome. A few things to know before you open a pull request.

### Licensing of contributions

VisTest is licensed under Apache-2.0 (see [LICENSE](LICENSE)). As section 5 of
the license says, a contribution you submit is under the same license, with no
further terms.

By submitting a pull request you confirm that you wrote the contribution
yourself, or have the right to submit it under Apache-2.0.

### What makes a good pull request

- One change per pull request.
- The suite passes, in the environment the library's users have:
  `pip install -e ".[browser,report,dev]"`, then `python -m pytest tests -n auto`.
  The tests read nothing outside this repository's own files
  (`tests/test_open_half.py` checks it).
- If you touched the comparison engine, run `python tests/benchmark.py --compare`
  and include the before/after numbers. A change that improves detection but
  raises the false-fail rate is not an improvement.
- New behaviour comes with a test. The synthetic corpus in `tests/` exists so
  that "it looked right on my screenshots" is never the evidence.
- Keep `ruff` clean: `ruff check vistest/ tests/`

### Reporting security issues

Do not open a public issue for a vulnerability. Use the repository's private
security advisory feature instead; the maintainer will respond there.
