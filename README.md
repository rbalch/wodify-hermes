# Wodify Hermes

A Hermes tool that books CrossFit classes on [Wodify](https://www.wodify.com/)
over pure HTTP — no browser automation. It is a Python port of the original
`wodify-openclaw` TypeScript plugin, reimplementing Wodify's OutSystems
`OnlineSalesPage` screenservice flow (session bootstrap → CSRF → login →
schedule → booking).

## Installation

Requires Python 3.12+.

```sh
git clone https://github.com/rbalch/wodify-hermes.git
cd wodify-hermes
python -m venv .venv && source .venv/bin/activate   # recommended
pip install .
```

That puts the `hermes-wodify` console script on your `PATH`. To install without
cloning first:

```sh
pip install git+https://github.com/rbalch/wodify-hermes.git
```

(If you use [uv](https://docs.astral.sh/uv/), swap `pip install` for
`uv pip install`.)

## First-time setup

Run discovery once to log in and resolve every config value (customer IDs,
location ID, membership ID, and the OutSystems version hashes), persisting them
to `~/.hermes/wodify/config.json`:

```sh
hermes-wodify discover \
  --gym-subdomain delraybeach \
  --email you@example.com \
  --password "your-password"
```

`login` performs the same discovery and can be run non-interactively from the
saved config or environment variables:

```sh
hermes-wodify login --email you@example.com --password "your-password"
```

## Commands

| Command | Purpose |
|---------|---------|
| `discover` | Interactive first-time setup; resolves & saves all config values |
| `login` | Authenticate and persist the discovered session config |
| `get-classes` | Fetch the schedule for a date |
| `book` | Book a class by ID |

### `get-classes`

```sh
hermes-wodify get-classes --date 2026-07-02
hermes-wodify get-classes --date 2026-07-02 --program-filter 119335
```

- `--date` is required (`YYYY-MM-DD`).
- `--program-filter` takes one or more **numeric program IDs** (comma-separated),
  not program names. Known IDs: CrossFit=`119335`, Open Gym=`119416`,
  Off Hours=`134852`. Omit it to query all three defaults.

### `book`

```sh
hermes-wodify book 176806811                 # class ID from get-classes
hermes-wodify book 176806811 --program-id 119335
```

Booking is a **real** reservation against your Wodify account. It auto-logs-in
and uses the `membership_id` from your config (which rotates on renewal — re-run
`discover` if a booking fails with "no active membership").

## Configuration

All state lives in `~/.hermes/wodify/config.json`:

```json
{
  "gym_subdomain": "delraybeach",
  "base_url": "https://delraybeach.wodify.com",
  "email": "you@example.com",
  "password": "...",
  "customer_id": "123",
  "customer_hex": "xxx",
  "location_id": 1234,
  "membership_id": "xxx",
  "version_hashes": { "moduleVersion": "...", "login": "...", "schedule": "...", "...": "..." }
}
```

Credentials also fall back to environment variables when absent from the file:
`WODIFY_EMAIL`, `WODIFY_PASSWORD`, `WODIFY_BASE_URL` (and `WODIFY_GYM_SUBDOMAIN`
for the `login`/`discover` `--gym-subdomain` flag).

## Development

Install editable, with test dependencies:

```sh
git clone https://github.com/rbalch/wodify-hermes.git
cd wodify-hermes
pip install -e ".[test]"
pytest -q          # mocked via httpx.MockTransport — no live calls
```

See the `Makefile` for shortcut targets (`make dev`, `make test`,
`make get-classes DATE=...`, `make login`, etc.).

## Version drift

Wodify's OutSystems deployment hashes change whenever they ship an update. The
client re-scrapes them on every login and treats the result as bookkeeping, not
failure:

- **Hashes changed, responses healthy** → the client updates in place and the
  CLI quietly persists the new hashes to config. No noise. (`version_changed`
  is set; `changed_endpoints` lists what moved.)
- **Hashes changed, responses unhealthy / a call fails** → the failure is
  surfaced with a note that the hashes changed, flagging a likely breaking
  change on Wodify's side rather than a bug here. `book`/`get-classes` append
  this note (`WodifyClient.drift_note()`) to their error output.

The WAF-walled endpoints (`booking`, `classAccess`, `membership*`) can't be
re-scraped, so their stored hashes are reused; if Wodify rotates one of those
behind the WAF, the corresponding call is where a breaking change would show up.

## Known gaps / not-yet-ported

These carried behaviors from the original OpenClaw plugin that are **not** in
the Python port yet:

- **Command parity.** No equivalent yet of the original `wodify_check_access` or
  `wodify_refresh_config` tools.
- **Hermes registration.** Invoked as the standalone `hermes-wodify` script, not
  yet as a `hermes wodify …` subcommand — no tool manifest exists.
- **WAF-walled hashes.** Wodify serves `MembershipType.mvc.js` behind an AWS WAF
  challenge, so `booking`/`classAccess`/`membership*` version hashes can't be
  scraped; discovery falls back to the cached values in config.
- **Output formatting.** `get-classes` prints raw model reprs, not a table.

## License

MIT — see [LICENSE](LICENSE).
