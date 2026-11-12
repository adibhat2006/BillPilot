"""Tests for health check and basic skeleton endpoints."""

import pytest
from app import create_app
import config

@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert "ai_provider" in data
    assert "ai_configured" in data
    assert "paypal_configured" in data
    assert "demo_mode" in data
    # Ensure no secrets leaked
    assert "PAYPAL_CLIENT_SECRET" not in str(data)
    assert "AI_API_KEY" not in str(data)

def test_index_serves_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"BillPilot" in response.data

def test_static_assets(client):
    res_js = client.get("/static/app.js")
    assert res_js.status_code == 200
    res_css = client.get("/static/style.css")
    assert res_css.status_code == 200
