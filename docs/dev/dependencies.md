# Dependencies: vulnerabilities and licences

Plan item v4, taken on 2026-10-13 against the wheel of 0.2.0.dev3 (the one on
TestPyPI). Each extra was installed into a fresh Python 3.11 environment, and
the resolved set was frozen and checked. Repeat it before every release; the
numbers are of that day's resolution.

## Vulnerabilities

```bash
python -m venv env && env/bin/pip install "vistest-0.2.0.dev3-py3-none-any.whl[full]"
env/bin/pip freeze --exclude vistest > freeze.txt
pip-audit --disable-pip --no-deps -r freeze.txt
```

pip-audit 2.10.1 (PyPI advisory database and OSV): **no known vulnerabilities**
in the core set (4 packages), `[browser]` (16), `[server]` (28), `[ldap]` (6)
and `[full]` (45). The check was seen to work: on `requests==2.25.0` the same
command reports 8 known vulnerabilities.

## Licences

Every package an extra brings, as `pip-licenses` reads its metadata:

| Package | Version | Licence | Comes with |
|---|---|---|---|
| allure-pytest | 2.16.2 | Apache Software License | full only |
| allure-python-commons | 2.16.2 | Apache Software License | full only |
| annotated-doc | 0.0.5 | MIT | server |
| annotated-types | 0.8.0 | MIT | server |
| anyio | 4.15.1 | MIT | server |
| attrs | 26.1.0 | MIT | full only |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) | server |
| charset-normalizer | 3.5.2 | MIT | server |
| click | 8.5.0 | BSD-3-Clause | server |
| fastapi | 0.143.0 | MIT | server |
| flatbuffers | 25.12.19 | Apache Software License | full only |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | browser |
| h11 | 0.16.0 | MIT License | server |
| httptools | 0.9.0 | MIT | server |
| idna | 3.20 | BSD-3-Clause | server |
| iniconfig | 2.3.1 | MIT | browser |
| ldap3 | 2.9.1 | GNU Lesser General Public License v3 (LGPLv3) | ldap |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | core, browser, server, ldap |
| onnxruntime | 1.31.0 | MIT License | full only |
| opencv-python-headless | 5.0.0.93 | Apache Software License | core, browser, server, ldap |
| opentelemetry-api | 1.45.1 | Apache-2.0 | server |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | browser |
| pillow | 12.3.0 | MIT-CMU | core, browser, server, ldap |
| playwright | 1.63.0 | Apache-2.0 | browser |
| pluggy | 1.6.0 | MIT License | browser |
| protobuf | 7.36.2 | 3-Clause BSD License | full only |
| pyasn1 | 0.6.4 | BSD-2-Clause | ldap |
| pydantic | 2.14.1 | MIT | server |
| pydantic_core | 2.50.1 | MIT | server |
| pyee | 13.0.1 | MIT License | browser |
| Pygments | 2.21.0 | BSD-2-Clause | browser |
| pytest | 9.1.1 | MIT | browser |
| pytest-playwright | 0.10.0 | Apache Software License | browser |
| python-dotenv | 1.2.4 | BSD-3-Clause | server |
| python-multipart | 0.0.32 | Apache-2.0 | server |
| python-slugify | 9.1.3 | MIT | browser |
| PyYAML | 6.0.3 | MIT License | core, browser, server, ldap |
| requests | 2.34.2 | Apache Software License | server |
| starlette | 1.8.0 | BSD-3-Clause | server |
| text-unidecode | 1.3 | Artistic License; GNU General Public License (GPL); GNU General Public License v2 or later (GPLv2+) | browser |
| typing-inspection | 0.4.4 | MIT | server |
| typing_extensions | 4.16.0 | PSF-2.0 | browser, server |
| urllib3 | 2.8.0 | MIT | server |
| uvicorn | 0.54.0 | BSD-3-Clause | server |
| uvloop | 0.23.0 | Apache Software License; MIT License | server |
| watchfiles | 1.3.0 | MIT License | server |
| websockets | 17.2 | BSD-3-Clause | server |

Nothing here is redistributed by VisTest: the wheel carries none of these
packages, a user's pip installs them. Two deserve a line:

- **text-unidecode** (Artistic or GPLv2+) comes with `[browser]` through
  `pytest-playwright` → `python-slugify`. Installing it is the user's pip
  doing what pytest-playwright asks; it does not change the licence of
  VisTest's own code.
- **ldap3** (LGPLv3) comes only with `[ldap]` and is imported, not copied.

## What the package itself carries

- `vistest/library/fonts/canary-sans.ttf`: a subset of DejaVu Sans 2.37, its
  internal names still «DejaVu Sans» (the Bitstream Vera licence forbids a
  modified font to be called Bitstream or Vera, and it is not). The licence
  travels next to it, unchanged: `vistest/library/fonts/LICENSE`.
- The corpus fonts (`tests/browser_corpus/templates/fonts/`, sdist only): the
  same DejaVu subsets, with their `LICENSE`.
- The review interface (`frontend/`) has no third-party code: every script and
  style in it is VisTest's own.

## Data in the tree

On `review/v4v5` (from main `b9966c7`): no pictures, databases, archives, keys
or `.env` files outside `tests/benchmark_corpus/` and `tests/browser_corpus/`;
every e-mail address, host and private address in the tree is a placeholder
(`example.com`, `acme.local`, `10.0.0.x`, `shop.example`); no home directory
of a person. `scripts/audit_dist.py` finds nothing in the built wheel and
sdist.
