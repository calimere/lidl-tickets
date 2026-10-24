import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Recharge l'application sur une base et un dossier de données vierges."""
    monkeypatch.setenv("LIDL_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LIDL_SECRET_KEY", raising=False)
    monkeypatch.delenv("LIDL_REGISTRATION_CODE", raising=False)
    for name in ("config", "db", "accounts", "auth", "lidl_client", "app"):
        sys.modules.pop(name, None)
    import accounts, app, config, db  # noqa: E401
    return {"config": config, "db": db, "accounts": accounts, "app": app}


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient
    with TestClient(env["app"].app) as c:
        yield c


def make_detail(ticket_id="T1", date="2024-03-01T10:00:00", price="1,00"):
    return {"id": ticket_id, "date": date, "totalAmount": 1.0,
            "store": {"name": "Lidl", "locality": "Marseille"},
            "itemsLine": [{"codeInput": "123", "description": "Lait", "currentUnitPrice": price,
                           "quantity": "1", "isWeight": False, "originalAmount": price,
                           "discounts": []}]}
