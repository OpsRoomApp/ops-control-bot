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
| `/roadmap` | OPS ROOM development roadmap |

### User

| Command | Description |
|---------|-------------|
| `/profile` | View your OPS ROOM profile |
| `/profile-set` | Set simulator, network, OPS ROOM version |
| `/logbook` | View your flight logs |
| `/log-flight` | [Owner] Manually log a flight |

### Support

| Command | Description |
|---------|-------------|
| `/bug` | Report a bug (modal with forum thread) |
| `/support` | Create a support ticket (forum thread) |

### Admin

| Command | Description |
|---------|-------------|
| `/admin-health` | [Owner] Detailed bot health |
| `/admin-logs` | [Owner] View audit logs |
| `/admin-db-stats` | [Owner] Database statistics |

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
| `SIMBRIEF_API_KEY` | No | — | SimBrief API key for `/ofp` and `/link-simbrief` |
| `GITHUB_REPO` | No | `OpsRoomApp/ops-room-releases` | GitHub repo for `/latest` |
| `OPSROOM_RELEASES_API` | No | `https://opsroom.live/api/update.json` | Release manifest URL |
| `BUG_FORUM_CHANNEL_ID` | No | — | Forum channel for bug reports (optional) |
| `SUPPORT_FORUM_CHANNEL_ID` | No | — | Forum channel for support tickets (optional) |

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
| **SimBrief** | No | `SIMBRIEF_API_KEY` in `.env` | For `/ofp` and `/link-simbrief` |
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
- SimBrief requires paid API key for authenticated access
- OpenSky anonymous tier rate-limited to ~10 req/min
- Single-guild deployment per spec
- Windows: SIGTERM not supported (use Ctrl+C)
- /flight vatsim (legacy) duplicates /vatsim-status (newer)
- GitHub release service ready but not yet wired to auto-announce

---

## License

Proprietary. OPS ROOM ecosystem. All rights reserved.

---

*OPS CONTROL v1.0.0 — Built for the OPS ROOM aviation operations platform.*
