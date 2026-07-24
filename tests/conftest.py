import os

# Credenciales dummy para que los servicios (Firestore/Gemini) se instancien sin
# error de validación al importar api.main — ninguno de estos tests hace llamadas
# reales a GCP/Gemini, así que no hace falta que las credenciales sean válidas.
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("GCP_PROJECT", "test-project")

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
