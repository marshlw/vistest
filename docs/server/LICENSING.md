# Licensing: the server's key and editions

*Читать по-русски: [LICENSING.ru.md](LICENSING.ru.md)*

Two things share the word «licence» here, and mixing them up causes trouble, so
they are separated on purpose:

- **The source licence is AGPL-3.0-or-later.** That is what governs the code:
  read it, modify it, run it, and if you run a modified version as a network
  service, publish your changes. Nothing below takes that away.
- **A commercial licence key** is what a paying customer receives. It states
  who bought it, until when, and how many projects and users it covers.

### Editions

| | Free tier | With a key |
|---|---|---|
| Engine, UI, CI integration, clients | everything | everything |
| Connected projects | 2 | as bought |
| Active users | 5 | as bought |
| Support and updates | community | as agreed |

The free tier is not a crippled demo: the engine, the review flow and every
integration are the same. The limits are the whole difference.

### Installing a key

Three places, checked in this order — the environment wins so that a container
is configured the way containers are configured:

```bash
VISTEST_LICENSE="eyJlZGl0aW9uIjoicHJvIiw…"   # environment
.vistest/license.key                          # the data volume
```

…or paste it into **Settings → Licence** in the interface. It is verified on
the spot and saved to the data volume.

Verification is entirely offline: the key carries an RSA signature, the public
half is compiled into VisTest, and checking it needs no network and no extra
package. An installation inside a closed perimeter never has to reach anything.

### When a term ends

Nothing is taken away from the customer. Baselines stay readable, history stays
readable, review keeps working — those are your data and your decisions.

For thirty days after the end date the installation works normally and says so
on every screen. After that, and only then, **new runs are refused** with a 402
and an explanation. Installing a current key restores everything immediately.

### Issuing keys (for the vendor)

```bash
python scripts/issue_license.py --new-key ~/.vistest-signing/private.pem
python scripts/issue_license.py --key ~/.vistest-signing/private.pem \
    --customer "ACME GmbH" --expires 2027-09-01 --projects 10 --users 25
```

The private half never goes into the repository, a backup that leaves your
machine, or a support ticket.

### An honest note about enforcement

The source is open, so anyone can delete the check and rebuild. That is a
property of the AGPL, not a hole to be plugged, and no amount of obfuscation
would change it. What the key does is make the terms explicit and checkable —
so that an honest customer knows what they bought, and a renewal is a
conversation rather than an audit.
