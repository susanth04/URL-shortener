from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl, field_validator
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter, Gauge
import asyncpg
import asyncio
import string
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom Prometheus metrics
# ---------------------------------------------------------------------------
REDIRECTS_TOTAL = Counter(
    "url_shortener_redirects_total",
    "Total number of successful URL redirects",
)
URLS_CREATED_TOTAL = Counter(
    "url_shortener_urls_created_total",
    "Total number of short URLs created",
)
DB_POOL_SIZE = Gauge(
    "url_shortener_db_pool_size",
    "Current size of the asyncpg connection pool",
)

app = FastAPI(
    title="URL Shortener",
    description="A fast, async URL shortener built with FastAPI and PostgreSQL",
    version="1.0.0",
)

# Auto-instrument all routes — exposes /metrics
Instrumentator().instrument(app).expose(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/urlshortener")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
BASE62 = string.digits + string.ascii_lowercase + string.ascii_uppercase

pool = None


# ---------------------------------------------------------------------------
# DB startup with retry (waits for Postgres to be ready)
# ---------------------------------------------------------------------------
async def create_pool_with_retry(dsn: str, retries: int = 10, delay: float = 3.0):
    for attempt in range(1, retries + 1):
        try:
            p = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
            logger.info("Database connection pool created.")
            return p
        except Exception as exc:
            logger.warning("DB not ready (attempt %d/%d): %s", attempt, retries, exc)
            if attempt == retries:
                raise
            await asyncio.sleep(delay)


@app.on_event("startup")
async def startup():
    global pool
    pool = await create_pool_with_retry(DB_URL)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS urls (
                id          SERIAL PRIMARY KEY,
                long_url    TEXT NOT NULL,
                short_code  TEXT UNIQUE,
                click_count INT DEFAULT 0,
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        # Backfill short_code for any legacy rows
        await conn.execute("""
            UPDATE urls SET short_code = id::TEXT WHERE short_code IS NULL
        """)
    DB_POOL_SIZE.set(pool.get_size())
    logger.info("Database schema ready.")


@app.on_event("shutdown")
async def shutdown():
    if pool:
        await pool.close()
        logger.info("Database pool closed.")


# ---------------------------------------------------------------------------
# Base-62 helpers
# ---------------------------------------------------------------------------
def encode_base62(num: int) -> str:
    if num == 0:
        return BASE62[0]
    arr = []
    base = len(BASE62)
    while num:
        num, rem = divmod(num, base)
        arr.append(BASE62[rem])
    return "".join(reversed(arr))


def decode_base62(code: str) -> int:
    url_id = 0
    base = len(BASE62)
    for char in code:
        if char not in BASE62:
            raise ValueError(f"Invalid character in short code: {char!r}")
        url_id = url_id * base + BASE62.index(char)
    return url_id


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ShortenRequest(BaseModel):
    url: HttpUrl

    @field_validator("url", mode="before")
    @classmethod
    def must_be_http(cls, v: str) -> str:
        if not str(v).startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class ShortenResponse(BaseModel):
    short_code: str
    short_url: str
    long_url: str


class StatsResponse(BaseModel):
    short_code: str
    long_url: str
    click_count: int
    created_at: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def frontend():
    """Serve the frontend SPA."""
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    return JSONResponse({"message": "URL Shortener API — visit /docs"})


@app.get("/health", tags=["System"])
async def health():
    """Liveness / readiness probe."""
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return JSONResponse({"status": "ok", "database": "reachable"})
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unreachable: {exc}")


@app.post("/shorten", response_model=ShortenResponse, tags=["URLs"])
async def shorten(req: ShortenRequest):
    """Accept a long URL and return a shortened code."""
    long_url = str(req.url)
    is_new = False

    async with pool.acquire() as conn:
        # De-duplicate: return existing code if URL was already shortened
        existing = await conn.fetchrow(
            "SELECT id, short_code FROM urls WHERE long_url = $1", long_url
        )
        if existing and existing["short_code"]:
            short_code = existing["short_code"]
        else:
            row = await conn.fetchrow(
                "INSERT INTO urls (long_url) VALUES ($1) RETURNING id", long_url
            )
            short_code = encode_base62(row["id"])
            await conn.execute(
                "UPDATE urls SET short_code = $1 WHERE id = $2",
                short_code, row["id"],
            )
            is_new = True

    if is_new:
        URLS_CREATED_TOTAL.inc()
        DB_POOL_SIZE.set(pool.get_size())

    return ShortenResponse(
        short_code=short_code,
        short_url=f"{BASE_URL}/{short_code}",
        long_url=long_url,
    )


@app.get("/stats/{short_code}", response_model=StatsResponse, tags=["URLs"])
async def stats(short_code: str):
    """Return click statistics for a short code."""
    try:
        url_id = decode_base62(short_code)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid short code")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT long_url, click_count, created_at FROM urls WHERE id = $1", url_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Short code not found")

    return StatsResponse(
        short_code=short_code,
        long_url=row["long_url"],
        click_count=row["click_count"],
        created_at=row["created_at"].isoformat(),
    )


@app.delete("/urls/{short_code}", tags=["URLs"])
async def delete_url(short_code: str):
    """Delete a shortened URL entry."""
    try:
        url_id = decode_base62(short_code)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid short code")

    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM urls WHERE id = $1", url_id)

    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Short code not found")

    return {"detail": f"Deleted /{short_code}"}


@app.get("/{short_code}", tags=["URLs"])
async def redirect(short_code: str):
    """Redirect to the original URL and increment the click counter."""
    try:
        url_id = decode_base62(short_code)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid short code")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT long_url FROM urls WHERE id = $1", url_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="URL not found")
        await conn.execute(
            "UPDATE urls SET click_count = click_count + 1 WHERE id = $1", url_id
        )

    REDIRECTS_TOTAL.inc()
    return RedirectResponse(url=row["long_url"], status_code=302)