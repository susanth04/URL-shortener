# SnapURL — Fast URL Shortener
so im just curious about System design so just randomly created a VM and a python script to test out a url shortener with base62 encoding and and its dockerized. uses postgreSQL ,ik its basic but im just starting out thanks :)
## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI (Python 3.12) |
| Database | PostgreSQL 16 |
| Async DB driver | asyncpg |
| Web server | Uvicorn |
| Containerization | Docker + Docker Compose |
| Frontend | Vanilla HTML/CSS/JS |

---

## Project Structure

```
.
├── docker-compose.yaml          # Local dev (app + postgres)
├── docker-compose.prod.yaml     # Production (app only, external DB)
└── url-shortener/
    ├── Dockerfile               # Container definition
    ├── main.py                  # FastAPI application
    ├── requirements.txt         # Python dependencies
    └── static/
        └── index.html           # Frontend SPA
```

---

## Quick Start (Local)

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)

### Run locally

```bash
git clone https://github.com/susanth04/url-shortener.git
cd url-shortener

docker compose up -d --build
```

Open **http://localhost:8000** in your browser.

---

## API Reference

All endpoints are also documented at **`/docs`** (Swagger UI).

### `POST /shorten`
Shorten a URL.

**Request:**
```json
{ "url": "https://example.com/very/long/url" }
```

**Response:**
```json
{
  "short_code": "1",
  "short_url": "http://localhost:8000/1",
  "long_url": "https://example.com/very/long/url"
}
```

---

### `GET /{short_code}`
Redirect to the original URL (increments click counter).

```bash
curl -L http://localhost:8000/1
# → 302 redirect to original URL
```

---

### `GET /stats/{short_code}`
Get statistics for a short link.

**Response:**
```json
{
  "short_code": "1",
  "long_url": "https://example.com/very/long/url",
  "click_count": 42,
  "created_at": "2026-07-04T10:00:00"
}
```

---

### `DELETE /urls/{short_code}`
Delete a shortened URL.

```bash
curl -X DELETE http://localhost:8000/urls/1
```

---

### `GET /health`
Health check — returns DB connectivity status.

```json
{ "status": "ok", "database": "reachable" }
```

---

## Deploying to AWS EC2

### 1. Launch an EC2 instance
- AMI: **Ubuntu 24.04 LTS**
- Instance type: `t3.small` or larger
- Security group: open ports `22` (SSH) and `8000` (HTTP)

### 2. SSH into your instance

```bash
ssh -i your-key.pem ubuntu@<EC2-PUBLIC-IP>
```

### 3. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu && newgrp docker
```

### 4. Create `docker-compose.yaml`

```bash
cat > docker-compose.yaml << 'EOF'
services:
  app:
    image: susanth04/url-shortener:latest
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@db:5432/urlshortener
      - BASE_URL=http://<YOUR-EC2-PUBLIC-IP>:8000
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

  db:
    image: postgres:16-alpine
    environment:
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
      - POSTGRES_DB=urlshortener
    volumes:
      - pgdata:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d urlshortener"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
EOF
```

### 5. Pull and start

```bash
docker compose up -d
```

Your app is now live at `http://<EC2-PUBLIC-IP>:8000` 🚀

---

## Docker Hub

The image is publicly available:

```bash
docker pull susanth04/url-shortener:latest
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@db:5432/urlshortener` | Postgres connection string |
| `BASE_URL` | `http://localhost:8000` | Public base URL for short links |

---

## Useful Commands

```bash
# View logs
docker compose logs -f app

# Check container status
docker compose ps

# Stop everything (data preserved)
docker compose down

# Stop and wipe database
docker compose down -v

# Restart app only
docker compose restart app

# Open a DB shell
docker exec -it url-shortener-db psql -U postgres -d urlshortener
```

---

## How It Works

1. A long URL is stored in PostgreSQL and gets an auto-increment `id`
2. The `id` is encoded to **Base62** (digits + lowercase + uppercase = 62 chars)
3. This produces a short, URL-safe code like `1`, `a`, `3Kx`
4. When someone visits the short URL, the code is decoded back to the `id`
5. The original URL is fetched from DB and a `302 redirect` is returned

**Why Base62?** 1 million URLs only needs a 4-character code. Compact and clean.

---

## License

MIT © [Susanth](https://github.com/susanth04)
