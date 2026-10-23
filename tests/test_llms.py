"""/llms.txt is served free and points agents at PUBLIC_BASE_URL."""
import os, subprocess, sys

def test_llms_txt_uses_public_base():
    code = r'''
from fastapi.testclient import TestClient
from app.main import app
r = TestClient(app).get("/llms.txt")
assert r.status_code == 200, r.status_code
assert r.headers["content-type"].startswith("text/plain")
assert "https://tools.example.org" in r.text and "onrender.com" not in r.text, r.text[:300]
'''
    env = {**os.environ, "PUBLIC_BASE_URL": "https://tools.example.org/", "PYTHONPATH": "."}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_api_catalog_uses_public_base():
    code = r'''
from fastapi.testclient import TestClient
from app.main import app
r = TestClient(app).get("/.well-known/api-catalog")
assert r.status_code == 200, r.status_code
assert r.headers["content-type"].startswith("application/linkset+json")
ls = r.json()["linkset"][0]
assert ls["anchor"] == "https://tools.example.org"
assert ls["service-desc"][0]["href"] == "https://tools.example.org/openapi.json"
assert ls["status"][0]["href"] == "https://tools.example.org/health"
'''
    env = {**os.environ, "PUBLIC_BASE_URL": "https://tools.example.org/", "PYTHONPATH": "."}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
