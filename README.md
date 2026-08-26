<img src="proxfy/static/logo.png" alt="Proxfy" width="150">

# Proxfy

*[Deutsche Fassung](README.de.md)*

**Checks whether your Proxmox backups actually restore — and whether the
application still runs afterwards.**

Proxmox Backup Server verifies chunk checksums. That proves the bytes are
intact. It does not prove a working machine comes out of them. Proxfy restores a
backup under a throwaway VMID, boots it in isolation, runs real functional
checks — service active, port listening, web interface responding, database
returning a fresh record — and cleans up afterwards.

Works the same for **VMs and LXC containers**.

> **Nothing runs by itself.** No built-in schedule, no cron entry. A run only
> happens through a click or through a schedule you created yourself.

---

## Installation

On the Proxmox host:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/bonderaustria/proxfy/main/proxfy.sh)"
```

The script asks for container ID, address, gateway and resources, creates an
unprivileged container, sets up SSH access to the hypervisor and installs
everything into it. Afterwards the interface waits for its first account at
`http://<address>:8099/`.

Without questions:

```bash
PROXFY_IP=192.168.1.50/24 PROXFY_GW=192.168.1.1 PROXFY_UNATTENDED=1 \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/bonderaustria/proxfy/main/proxfy.sh)"
```

Check first, without creating anything:

```bash
PROXFY_DRY_RUN=1 PROXFY_UNATTENDED=1 PROXFY_IP=192.168.1.50/24 PROXFY_GW=192.168.1.1 \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/bonderaustria/proxfy/main/proxfy.sh)"
```

That shows the detected defaults, whether the container ID is free and whether
the address answers — and creates nothing.

**Requirements:** Proxmox VE 8 or 9, a storage holding backups (PBS or a
directory with vzdump files), and internet access inside the container to
install Node.

### From a cloned directory

```bash
git clone https://github.com/bonderaustria/proxfy.git
cd proxfy && bash proxfy.sh
```

### Uninstalling

```bash
bash /opt/proxfy/uninstall.sh
```

`--yes` skips the questions, `--keep-data` keeps the configuration along with
the history and user databases. Backups, backup storage and production guests
are never touched.

---

## Updating

Run the same command again:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/bonderaustria/proxfy/main/proxfy.sh)"
```

The script recognises the existing installation — it looks for the container
holding `/opt/proxfy/config.yaml` — and updates only that one. It asks for
neither address nor resources and creates no second container.

Only the program code and interface are renewed. Untouched:

| File | Contents |
|---|---|
| `config.yaml` | hypervisor, storages, bridges, timeouts |
| `auth.env` | secrets of the login service |
| `auth.db` | accounts, passwords, second factor, sessions |
| `proxfy.db` | schedules, history, test guests, IP pool, settings |

Before every update those four are copied to
`/opt/proxfy-sicherung/<timestamp>/` inside the container; the last five states
are kept. Going back is a matter of copying them into place and running
`systemctl restart proxfy proxfy-auth`.

See what would happen first:

```bash
PROXFY_DRY_RUN=1 PROXFY_UNATTENDED=1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/bonderaustria/proxfy/main/proxfy.sh)"
```

To create a second, independent installation despite an existing one:

```bash
PROXFY_NEU=1 PROXFY_IP=192.168.1.51/24 PROXFY_GW=192.168.1.1 \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/bonderaustria/proxfy/main/proxfy.sh)"
```

---

## Behind a reverse proxy

Making Proxfy reachable from the internet means: the proxy takes the
connection, terminates TLS and passes it on to port 8099. Two sides need
configuring for that to hold — the proxy and Proxfy itself.

### Why it does not work without configuration

The login service checks where a request comes from. With a proxy in front it
arrives carrying the proxy's address — `https://verify.example.org` instead of
`http://192.168.1.35:8099`. Proxfy does not know that address at first and turns
it away:

```
{"message":"Invalid origin","code":"INVALID_ORIGIN"}
```

This is not a bug but the protection that keeps a foreign site from making
requests in the name of a signed-in person. It only needs to be told which
address is legitimate.

### 1. In Nginx Proxy Manager

**Hosts → Proxy Hosts → Add Proxy Host**, tab *Details*:

| Field | Value |
|---|---|
| Domain Names | `verify.example.org` |
| Scheme | `http` |
| Forward Hostname / IP | address of the Proxfy container |
| Forward Port | `8099` |
| Cache Assets | off |
| Block Common Exploits | on |
| Websockets Support | on |

Tab *SSL*: pick a certificate, switch on **Force SSL** and **HTTP/2 Support**.

Tab *Advanced* — this block is the decisive part:

```nginx
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 3600s;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

`proxy_buffering off` is not optional. Proxfy sends the log of a running test as
a continuous stream (server-sent events). With buffering, Nginx collects that
stream and hands it over only at the end — the interface then looks frozen even
though the run continues normally. `proxy_read_timeout` has to outlast the
longest run, otherwise the connection breaks in the middle of a restore.

### 2. In Proxfy

**Settings → Access and network**:

- **Address from outside** — exactly the address the browser sees Proxfy under,
  with scheme and without a path: `https://verify.example.org`. Proxfy writes it
  into `auth.env` and restarts the login service; the address on your own
  network stays allowed alongside it.
- **Running behind a reverse proxy** — Proxfy then reads the real origin address
  from `X-Forwarded-For`. Without it the login lockout would only ever see the
  proxy's address and would lock out everyone on a single failed attempt.
- **Session cookie over HTTPS only** — right as soon as Proxfy is reachable from
  the internet. After that you get in **only through the proxy**: the browser no
  longer hands out the cookie over `http://`. Switch it on once the path through
  the proxy demonstrably works.

The **Test the path through the proxy** button calls the configured address and
reports what came back. If something other than Proxfy answers, the proxy points
at the wrong machine.

### When it does not work

| Symptom | Cause |
|---|---|
| `Invalid origin` on sign-in | address from outside missing or different — scheme, name and port must match the address bar exactly |
| Sign-in fails silently, cookie missing | “Session cookie over HTTPS only” is on but the call went over `http://` |
| Live log of a run stands still | `proxy_buffering off` is missing |
| Connection drops after a few minutes | `proxy_read_timeout` too short |
| A lockout after one failed attempt hits everyone | “Running behind a reverse proxy” is off |
| The test reports “not reachable” although it works from outside | the container cannot reach the public address (no NAT loopback). Not a fault of Proxfy |
| 502 from the proxy | wrong target address or port, or the service is down: `systemctl status proxfy` |

You cannot lock yourself out permanently: this group remembers the previous
state and restores it unless the change is confirmed within ten minutes. And
`proxfy config reset` on the container's command line clears every setting made
through the interface.

---

## Languages

The interface speaks **German and English**, switchable in the header. The
language belongs to the account, so a German and an English account can work
side by side. Until someone signs in, the sign-in page follows the browser.

German is the source: it stands in the markup and in the code as it is
displayed, English lives beside it in
[`proxfy/static/i18n-en.js`](proxfy/static/i18n-en.js) and, for server messages,
in [`proxfy/texte.py`](proxfy/texte.py) — keyed by the German text itself.
Adding a feature therefore means writing the German text and adding one English
line. If it is missing, German appears: visible and fixable in one line, rather
than an empty spot or a raw key.

---

## Architecture

Proxfy runs in its own LXC, not on the hypervisor — Python there is “externally
managed”, and third-party software does not belong on the hypervisor itself.

```
   Browser
      │  port 8099  (the only door to the outside)
      ▼
 ┌─────────────────────────────────────────┐
 │  LXC "proxfy"                           │
 │                                         │
 │   Python  ──── checks every request ──► │
 │   :8099          Node + Better Auth     │
 │                  127.0.0.1:8100         │
 │                  (loopback only)        │
 └──────────────┬──────────────────────────┘
                │ SSH with a key
                ▼
        Proxmox VE (pct, qm, pvesm)
```

---

## What a run does

```
pick backup  →  plan the network (preflight)  →  restore
      →  strip the network cards  →  boot ISOLATED  →  checks from inside
      →  [routed only] assign IP, move onto the LAN  →  checks from outside
      →  apply the lifetime policy
```

An LXC is verified in about 30 seconds, a 27 GB VM in a little over two minutes.

---

## Safety

This is the part that decides between usefulness and damage.

### The test guest gets exactly one network card

A restored guest carries **all** network cards of the original. A DNS server or
reverse proxy easily hangs in six VLANs with six cards, each with a static
production IP. Rewriting only `net0` would leave the test guest on the network
under the original addresses through the remaining cards, colliding with the
running original.

Therefore **all** cards except `net0` are deleted before the first boot and
`net0` is set anew. If a card remains afterwards, the run aborts.

### No live restore

`qmrestore --live-restore` starts the VM as part of the restore command, with
the network configuration from the backup. There is no window in which the cards
could be corrected beforehand — the VM would sit on the production network under
the original addresses for the duration of the restore. The approach cannot be
made safe and is not used. In practice giving it up costs nothing, because the
checks would only start after the restore finished anyway.

### The two network modes

**`isolated`** (default) — a bridge without uplink, created at runtime. The
guest can physically reach nothing. Checks run from inside through the QEMU
guest agent or `pct exec`.

**`routed`** — the guest gets an address from the configured pool so services
can be checked the way a client sees them. Secured by:

| Protection | Effect |
|---|---|
| **IP preflight** | Four independent probes: ARP duplicate detection, ICMP, neighbour table, guest configurations. One hit aborts — nothing is guessed. |
| **Addresses in use** | Addresses of running test guests are blocked. |
| **Fresh MAC** | Always generated anew, locally administered. DHCP reservations of the original do not apply. |
| **Isolated first boot** | The guest carries the original addresses until they are overwritten. During that phase it has no network path. |
| **Two-step transition** | Set the IP first, then switch the bridge. Never the other way round. |

> The preflight is only reliable within the local segment. An address from
> another VLAN cannot be checked over ARP — there, ICMP and the configuration
> search remain as weaker probes.

### Further invariants

- **Scratch range 9000–9099.** Test guests are created only there. Every
  destructive action checks that again. The range belongs to Proxfy alone.
- **Restores in progress are off limits.** A guest sitting there as “stopped”
  with 0 GB is not necessarily orphaned — that is exactly what a running
  `qmrestore` looks like. Before destroying anything, running restore processes
  are checked for.
- **An unmistakable name.** The test guest is called `proxfy-<original>`, never
  the same as the original, and carries the tag `proxfy-test`.
- **Cleanup in `finally`.** At service start, leftovers of a crashed run are
  removed as well, including those with `lock=create`.

---

## Checks

Checks are edited as rows: pick a type, fill in the matching fields, toggle
“from outside” and “required”. The JSON view remains as an expert mode.

| Type | Required fields | Purpose |
|---|---|---|
| `boot` | — | the guest responds at all (always runs first) |
| `service` | `unit` | systemd service is `active` |
| `port` | `port` | TCP port is listening |
| `http` | `url` | status code, optionally `expect_body` as a regex |
| `tls` | — | TLS handshake and remaining validity, `port`, `min_days` |
| `command` | `run` or `argv` | exit code, optionally `expect_output` as a regex |
| `file` | `path` | file exists, optionally `min_bytes` |
| `newest_file` | `path` | age of the newest file, `max_age_hours` |
| `file_count` | `path` | number of files, `min_count`, `pattern` |
| `postgres` / `mysql` | — | a real query, optionally `expect` |
| `db_fresh` | `query` | age of the newest record, `max_age_hours` |

Extra fields: `external: true` checks from the host against the assigned address
(requires `routed`), `required: false` does not count against the overall
result.

### The test guest as a workbench

Two functions need a **running** test guest — that is, a run with the lifetime
“time window” or “keep”. Both only ever touch test guests, never production VMs
or containers.

**Discover from test guest** examines the running test guest and proposes
finished checks: running systemd services beyond the base set, listening ports
along with process names, Docker containers, detected databases. What it finds
is what is actually **in the backup**.

**Trial run** (▶ on every row) runs a single check straight against the running
test guest, without restoring again.

> Careful with `isolated`: services that pull data from the internet at startup
> fail without a network. paperless-ngx, for instance, fetches a wheel through
> `uv run` on every start and aborts in isolation with a DNS error. Such guests
> need `routed`, or the check is set to `required: false`.

---

## Lifetime of the test guest

| Policy | Behaviour |
|---|---|
| `destroy` | destroy right after the checks. The right choice for unattended runs. |
| `ttl` | stays for N minutes, then removed automatically |
| `manual` | stays until someone removes it under “Test guests” |

If a run fails **before** the guest was up, it is always destroyed — there would
be nothing to examine. If it fails afterwards, the policy applies: a guest that
failed is one you want to look at.

---

## Test addresses

Single addresses and ranges:

```
192.168.1.240                 single
192.168.1.240/24              single with prefix
192.168.1.15-38               range, short form
192.168.1.15-192.168.1.38     range, full form
```

A range allows **several runs at the same time** in `routed` mode — each run
takes the next free address and returns it afterwards. With a single address
only one guest was ever possible.

---

## Schedules

Time of day plus weekdays, with multiple selection of guests. The guests go
through the queue one after another, not in parallel — parallel restores would
fight over storage bandwidth and scratch slots.

Every schedule can be changed completely afterwards and triggered immediately
with “Run now”. Its recent runs are shown with the schedule itself.

---

## Users and roles

The **first** account created becomes super admin. After that the setup form is
closed for good.

| | Super admin | Admin | User |
|---|---|---|---|
| Start verifications, maintain schedules | yes | yes | yes |
| Own account and own second factor | yes | yes | yes |
| Create and remove users | all | users only | no |
| Reset two-factor | for everyone | for users only | no |
| Hand out roles | yes | no | no |
| Sign-in attempts, lift locks | yes | yes | no |
| Change settings | yes | no | no |

The last super admin can neither be deleted nor demoted — otherwise nobody could
change settings any more.

Everything is checked **server-side** at every endpoint. What the interface
hides is convenience, not protection.

### Signing in

Sessions live server-side in SQLite. The browser holds nothing but an `HttpOnly`
cookie — no token, no JWT, nothing JavaScript could read. Every session can be
ended server-side at once.

**Two-factor** (TOTP) can be enabled per account, with ten recovery codes. Until
the second factor is entered, **no** session exists.

**Sign-in attempts:**

| Attempts | Behaviour |
|---|---|
| 1–3 | no delay |
| 4, 5, 6+ | 2 s, 4 s, then 8 s |
| from 10 | locked for 15 minutes, even with the correct password |

Counted separately by origin IP and by user identifier; the worse of the two
applies. Requests are turned away **before** the login service ever sees the
password. The thresholds are adjustable.

---

## Settings

Everything is changeable in the interface. Because some values can lock you out,
they are secured in stages:

| Group | Safeguard |
|---|---|
| Defaults | none — they apply on the next run |
| Proxmox connection | connection test enforced, saving only possible afterwards |
| Access & network | password required, then a ten-minute rollback window |
| Lockout thresholds | password required |

The database only overlays the file. Way back in, should the interface become
unreachable:

```bash
python3 -m proxfy.cli config show
python3 -m proxfy.cli config reset
```

### Putting it on the internet

1. **TLS in front**, for example through a reverse proxy.
2. Set the address from outside under *Settings → Access and network*.
3. Switch on “Session cookie over HTTPS only”.
4. Switch on “Running behind a reverse proxy”, so the lockout sees the real
   origin address. **Only** if a proxy really is in front.
5. Enable two-factor for every account.

> The container holds an SSH key with root rights on the hypervisor. Whoever
> takes over the container takes over Proxmox.

---

## Command line

```bash
python3 -m proxfy.cli snapshots --latest-only
python3 -m proxfy.cli run --vmid 118
python3 -m proxfy.cli run --vmid 118 --mode routed --ip 192.168.1.240/24 --gateway 192.168.1.1
python3 -m proxfy.cli reap --force
python3 -m proxfy.cli config show
```

Exit code `0` when every run was verified.

---

## Limits

- **Without the QEMU guest agent**, VMs only ever tell you “it boots somehow”.
  Containers have the advantage: `pct exec` always works.
- **The preflight reports gateway addresses as taken** as well, because it
  searches guest configurations as text. Conservative, but occasionally a false
  alarm.
- **One run at a time.** Multiple selection queues them rather than
  parallelising.
- **No PDF report.** The data sits complete in SQLite, only the output is
  missing.

---

## Licence

MIT
