# Contributors

VisTest was designed and written by:

- **Kirill Kulagin** — original author, engine, service, UI, everything through
  the first public release.

---

## Contributing

Contributions are welcome. A few things to know before you open a pull request.

### Licensing of contributions

This project is dual-licensed: AGPL-3.0-or-later for everyone, and a commercial
license for those who need one (see [NOTICE](NOTICE)).

By submitting a pull request you confirm that:

1. you wrote the contribution yourself, or have the right to submit it;
2. you grant the copyright holder the right to distribute your contribution
   under **both** the AGPL and the commercial license.

If you cannot agree to that, open an issue describing the change instead — the
idea is still welcome, it just cannot be merged as your code.

### What makes a good pull request

- One change per pull request.
- `pytest tests/` passes, and `pytest tests/api` if you touched the service.
- If you touched the comparison engine, run `python tests/benchmark.py --compare`
  and include the before/after numbers. A change that improves detection but
  raises the false-fail rate is not an improvement.
- New behaviour comes with a test. The synthetic corpus in `tests/` exists so
  that "it looked right on my screenshots" is never the evidence.
- Keep `ruff` clean: `ruff check .`

### Reporting security issues

Do not open a public issue for a vulnerability. Use the repository's private
security advisory feature instead; the maintainer will respond there.
