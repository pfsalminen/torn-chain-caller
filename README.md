# Faction Chain Claims

A real-time chain claim system for Torn factions. Members can claim specific hit numbers during a chain, with live updates across all connected users.

## Features

- Authenticate via torn.com API key
- Claim individual hits or ranges (e.g., "55" or "55-60")
- Real-time updates via WebSocket
- Automatic cleanup of claims below current chain count
- Chain status display with timeout countdown

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

Open http://localhost:8000 in your browser.

## Deployment (Fly.io)

```bash
fly launch --no-deploy
fly deploy
```

## API Endpoints

- `POST /api/auth` - Validate API key, return user/faction info
- `GET /api/claims` - Get all claims for user's faction
- `POST /api/claims` - Add a claim (single or range)
- `DELETE /api/claims/{id}` - Delete own claim
- `GET /api/chain` - Get chain info, auto-delete old claims
- `WS /ws/{faction_id}` - Real-time updates

## Tech Stack

- FastAPI + WebSockets
- SQLite + SQLAlchemy
- Vanilla HTML/CSS/JS frontend
