import os

import psycopg
import pytest
from fastapi.testclient import TestClient

# Tests run against a real Postgres (docker compose up -d postgres), in their own
# database so they never touch the data of the running stack
SERVER_URL = "postgresql://pulse:pulse@localhost:5432"
os.environ.setdefault("DATABASE_URL", f"{SERVER_URL}/pulse_test")

with psycopg.connect(f"{SERVER_URL}/pulse", autocommit=True) as admin:
    if not admin.execute("SELECT 1 FROM pg_database WHERE datname = 'pulse_test'").fetchone():
        admin.execute("CREATE DATABASE pulse_test")

import main  # noqa: E402  (services/api, on the pytest pythonpath)


@pytest.fixture(scope="session")
def client():
    # Entering the client runs the API lifespan, which creates the schema
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def db(client):
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        yield conn


@pytest.fixture(autouse=True)
def clean_tables(db):
    db.execute("TRUNCATE monitors, checks RESTART IDENTITY CASCADE")


@pytest.fixture
def monitor(client):
    response = client.post("/api/monitors", json={"name": "Example", "url": "https://example.com"})
    return response.json()
