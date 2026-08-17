# OPS CONTROL

**OPS CONTROL** is the official Discord operations companion bot for the **OPS ROOM** aviation operations platform.

Built with Python 3.12+, discord.py 2.x, Pillow, and aiosqlite. Designed for Docker deployment on a VPS.

---

## Features

### Core Commands

| Command | Description |
|---------|-------------|
| `/status` | Bot health, version, latency, loaded modules |
| `/ping` | Check bot latency |

### Welcome System

| Feature | Description |
|---------|-------------|
| Automatic `on_member_join` | Generates custom welcome image (42px name, 30px date/time) posted to `#arrivals` |
| `/welcome` | [Owner] Manual welcome image test |

### Operations

| Command | Description |
|---------|-------------|
| `/announce` | [Admin] Send formatted announcements with embeds |
| `/notam add|list|remove` | [Admin] NOTAM management with priorities |
| `/vatsim-status` | VATSIM network status (pilots, controllers, ATIS) |
| `/flightwatch CALLSIGN` | Track a specific VATSIM aircraft |
| `/metar ICAO` | METAR weather for any airport |
| `/atis ICAO` | VATSIM ATIS for any airport |
| `/ofp` | Fetch latest SimBrief Operational Flight Plan |
| `/link-simbrief USERNAME` | Link Discord to SimBrief account |
| `/latest` | Latest OPS ROOM release info |
| `/changelog` | Recent OPS ROOM releases |
| `/roadmap` | OPS ROOM development roadmap |
| `/randomroute` | Generate a realistic random flight (Where2Fly primary, local fallback) |

### User

| Command | Description |
|---------|-------------|
| `/profile` | View your OPS ROOM profile |
| `/profile-set` | Set simulator, network, OPS ROOM version |
| `/logbook` | View your flight logs |
| `/log-flight` | [Owner] Manually log a flight |
| `/weather metar ICAO` | METAR via NOAA Aviation Weather Center |
| `/weather taf ICAO` | TAF forecast via NOAA |
| `/notam ICAO` | Active NOTAMs for an airport |
| `/sigmet` | Active SIGMET weather warnings |
| `/ops-status` | VATSIM network status by region |
| `/airport-status ICAO` | Traffic, controllers, METAR/TAF/NOTAM for an airport |
| `/airport-add` / `/airport-remove` | Save departure/arrival/alternate airports |
| `/preferences` | Notification preferences (releases, weather, VATSIM events) |

### Support

| Command | Description |
|---------|-------------|
| `/bug` | Report a bug (modal, posts to bug reports channel, mentions Owner + Developer) |
| `/feedback` | Submit feedback or a feature request (opens a public forum thread) |
| `/support` | Open the support panel (persistent panel with ticket buttons) |

### Admin

| Command | Description |
|---------|-------------|
| `/admin-health` | [Owner] Detailed bot health |
| `/admin-logs` | [Owner] View audit logs |
| `/admin-db-stats` | [Owner] Database statistics |
| `/help` | Command list grouped by permission level |
| `/purge N` | [Moderator+] Delete N messages |
| `/betatester add/remove` | [Beta Coordinator+] Manage beta tester roles |
| `/scambait-warning` | [Admin] Post the restricted-channel warning notice in the scambait channel |
| `/verify-setup` | [Admin] Post or refresh the persistent Verify button in the verification channel |
| `/roles` | Role selection (simulator, network, tester) |

---

## Quick Start (Local Development)

### Prerequisites

- Python 3.12+
- [Git](https://git-scm.com/)

### Setup

```bash
git clone <your-repo-url> ops-control-bot
cd ops-control-bot
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Discord bot token and IDs
```

### Running

```bash
cd src && python -m bot.main
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_TOKEN` | **Yes** | — | Discord bot token |
| `GUILD_ID` | **Yes** | — | Discord server (guild) ID |
| `OWNER_USER_ID` | **Yes** | — | Bot owner's Discord user ID |
| `ARRIVALS_CHANNEL_ID` | **Yes** | — | Channel for welcome messages |
| `TIMEZONE` | No | `UTC` | Timezone for timestamps |
| `DATABASE_PATH` | No | `data/ops-control.db` | SQLite database path |
| `LOG_PATH` | No | `logs/ops-control.log` | Log file path |
| `LOG_LEVEL` | No | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `SIMBRIEF_USER_ID` | No | — | SimBrief pilot ID (default for `/ofp`) |
| `SIMBRIEF_STATIC_ID` | No | — | SimBrief static ID (default; user links take priority) |
| `GITHUB_REPO` | No | `OpsRoomApp/ops-room-releases` | GitHub repo for `/latest` |
| `OPSROOM_RELEASES_API` | No | `https://opsroom.live/api/update.json` | Release manifest URL |
| `BUG_FORUM_CHANNEL_ID` | No | — | Forum channel for bug reports (optional) |
| `SUPPORT_FORUM_CHANNEL_ID` | No | — | Forum channel for support tickets (optional) |
| `DISCORD_ANNOUNCEMENT_CHANNEL` | No | — | Channel used by `/announce` and admin-panel announcements |
| `LOG_CHANNEL_ID` | No | — | Discord channel for operation log embeds |
| `BUG_REPORTS_CHANNEL_ID` | No | — | Channel for bug reports |
| `SUPPORT_CATEGORY_ID` | No | — | Category for private ticket channels |
| `TICKET_TRANSCRIPT_CHANNEL_ID` | No | — | Channel for ticket transcripts |
| `MODERATOR_ROLE_ID` | No | — | Moderator role for `/purge` |
| `SUPPORT_DISPATCH_ROLE_ID` | No | — | Support Dispatch role (ticket mentions) |
| `DEVELOPER_ROLE_ID` | No | — | Developer role (bug report mentions) |
| `BETA_COORDINATOR_ROLE_ID` | No | — | Beta Coordinator role for `/betatester` |
| `SCAMBAIT_CHANNEL_ID` | No | — | Channel that auto-soft-bans (timeout) anyone who posts in it |
| `SCAMBAIT_TIMEOUT_MINUTES` | No | `60` | Scambait soft-ban duration in minutes (Discord cap: 40320) |
| `VERIFY_CHANNEL_ID` | No | — | Channel hosting the persistent Verify button |
| `VERIFY_MEMBER_ROLE_ID` | No | — | Role granted when a member verifies |
| `VERIFY_UNVERIFIED_ROLE_ID` | No | — | Role removed when a member verifies (optional) |
| `VERIFIED_TESTER_ROLE_ID` / `PUBLIC_BETA_ROLE_ID` | No | — | Beta tester roles |
| `WHERE2FLY_ENABLED` | No | `true` | Enable Where2Fly route provider |
| `WHERE2FLY_API_TOKEN` | No | — | Where2Fly Bearer token (optional; local fallback used when empty) |
| `WHERE2FLY_API_BASE_URL` | No | `https://where2fly.today/` | Where2Fly API base URL |
| `WHERE2FLY_TIMEOUT_SECONDS` | No | `15` | Where2Fly request timeout |
| `PENDING_ACTION_POLL_SECONDS` | No | `15` | Pending-actions dispatcher poll interval |
| `PENDING_ACTION_MAX_ATTEMPTS` | No | `3` | Max dispatcher attempts per action |

---

## Docker Deployment (VPS)

```bash
cp .env.example .env
nano .env  # Add your DISCORD_TOKEN and IDs
docker compose up -d
docker compose logs -f
docker compose down  # Stop
```

---

## Discord Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create application **OPS CONTROL**
3. Bot → enable `Privileged Gateway Intents`: Presence, Server Members, Message Content
4. Copy token to `.env`
5. OAuth2 → URL Generator: `bot`, `applications.commands`; Permissions: Administrator

---

## Project Structure

```
ops-control-bot/
├── src/bot/
│   ├── main.py                # Entry point
│   ├── config.py              # Configuration loader
│   ├── logger.py              # Logging setup
│   ├── core/
│   │   ├── bot.py             # Discord client lifecycle
│   │   └── loader.py          # Cog auto-loader
│   ├── cogs/
│   │   ├── status.py          # /status, /ping
│   │   ├── welcome.py         # Welcome system
│   │   ├── announce.py        # /announce
│   │   ├── notam.py           # /notam
│   │   ├── flight_ops.py      # /flight *
│   │   ├── simbrief.py        # /link-simbrief, /ofp
│   │   ├── weather.py         # /metar
│   │   ├── atis.py            # /atis
│   │   ├── releases.py        # /latest, /roadmap
│   │   ├── vatsim.py          # /vatsim-status, /flightwatch
│   │   ├── bugs.py            # /bug (Modal)
│   │   ├── support.py         # /support
│   │   ├── profile.py         # /profile, /profile-set
│   │   ├── logbook.py         # /logbook, /log-flight
│   │   └── admin.py           # Admin commands
│   ├── services/
│   │   ├── welcome_image.py   # Pillow image generation
│   │   ├── audit.py           # Audit logging
│   │   └── github_release.py  # GitHub release automation
│   ├── database/
│   │   └── db.py              # SQLite + migrations
│   ├── api/
│   │   ├── __init__.py        # VATSIM, OpenSky, SimBrief, Weather
│   │   └── events.py          # Desktop Integration Events API
│   └── utils/
│       ├── helpers.py         # Shared utilities
│       ├── permissions.py     # Permission checks
│       └── checks.py          # Legacy alias
├── assets/
│   ├── welcome.png            # Welcome template
│   ├── opsroom-512.png        # Bot logo
│   └── fonts/                 # Sanchez fonts
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## API Keys

| Service | Required? | Key Location | Notes |
|---------|-----------|-------------|-------|
| **VATSIM** | No | — | Public API |
| **OpenSky** | No | — | Anonymous tier, ~10 req/min |
| **SimBrief** | No | — | Public XML fetcher (`api/xml.fetcher.php?userid=`) — no key required |
| **aviationweather.gov** | No | — | Free public METAR API |
| **GitHub Releases** | No | — | Public API for `/latest` |

---

## Database

Uses **SQLite** (WAL mode) with migration-friendly design.

### Tables

- `users` — Discord user profiles with simulator/network/version
- `guild_settings` — key-value guild configuration
- `notams` — NOTAM entries
- `announcements` — sent announcements
- `logs` — audit trail
- `simbrief_accounts` — SimBrief account links
- `bugs` — Bug reports
- `tickets` — Support tickets
- `flight_logs` — Flight history
- `events` — Desktop app integration events

### Migration to PostgreSQL

Schema uses standard SQL. To migrate:
1. Set `DATABASE_URL` in `.env`
2. Replace `aiosqlite` with `asyncpg` + SQLAlchemy async
3. Run schema creation against PostgreSQL

---

## Known Limitations

- No avatar on welcome images — per specification
- Where2Fly API token optional: bot runs in local-fallback route mode until `WHERE2FLY_API_TOKEN` is provided
- Where2Fly results are suggestions only — never presented as confirmed scheduled services
- OpenSky anonymous tier rate-limited to ~10 req/min
- Single-guild deployment per spec
- Windows: SIGTERM not supported (use Ctrl+C)
- /flight vatsim (legacy) duplicates /vatsim-status (newer)
- GitHub release service ready but not yet wired to auto-announce
- ADS-B real-world schedules reserved for the future OPS ROOM Dispatch Module

---

## License

Proprietary. OPS ROOM ecosystem. All rights reserved.

---

*OPS CONTROL v1.0.0 — Built for the OPS ROOM aviation operations platform.*
