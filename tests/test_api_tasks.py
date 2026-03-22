import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_task_queue():
    q = MagicMock()
    q.enqueue = AsyncMock(return_value=1)
    q.cancel = AsyncMock(return_value=True)
    q.running_count = MagicMock(return_value=0)
    return q


@pytest.fixture
async def client(tmp_path, monkeypatch, mock_task_queue):
    from src import config
    import src.logging_config as lc
    monkeypatch.setattr(config.settings, "tasks_dir", tmp_path / "tasks")
    monkeypatch.setattr(config.settings, "reports_dir", tmp_path / "reports")
    monkeypatch.setattr(config.settings, "log_dir", tmp_path / "logs")
    monkeypatch.setattr(config.settings, "log_console_enabled", False)
    monkeypatch.setattr(lc, "_logging_configured", False)
    (tmp_path / "tasks").mkdir()
    (tmp_path / "reports").mkdir()

    from src.main import create_app
    app = create_app()
    app.state.task_queue = mock_task_queue

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_create_task_returns_201(client):
    resp = await client.post("/api/v1/tasks", json={"stock_code": "000001.SZ"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["stock_code"] == "000001.SZ"
    assert data["is_existing"] is False


async def test_create_task_duplicate_returns_200(client):
    resp1 = await client.post("/api/v1/tasks", json={"stock_code": "000001.SZ"})
    assert resp1.status_code == 201

    resp2 = await client.post("/api/v1/tasks", json={"stock_code": "000001.SZ"})
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["is_existing"] is True
    assert data["task_id"] == resp1.json()["task_id"]


async def test_create_tasks_different_stocks(client):
    resp1 = await client.post("/api/v1/tasks", json={"stock_code": "000001.SZ"})
    resp2 = await client.post("/api/v1/tasks", json={"stock_code": "600000.SH"})
    assert resp1.status_code == 201
    assert resp2.status_code == 201
    assert resp1.json()["task_id"] != resp2.json()["task_id"]


async def test_get_task_includes_logs(client):
    resp = await client.post("/api/v1/tasks", json={"stock_code": "000001.SZ"})
    task_id = resp.json()["task_id"]
    from src.core.task_store import append_task_log
    append_task_log(task_id, "Stage1 技术分析师 开始")
    resp2 = await client.get(f"/api/v1/tasks/{task_id}")
    assert resp2.status_code == 200
    data = resp2.json()
    assert "logs" in data
    assert len(data["logs"]) == 1


async def test_get_task_not_found(client):
    resp = await client.get("/api/v1/tasks/TASK_NONEXISTENT")
    assert resp.status_code == 404


async def test_list_tasks(client):
    await client.post("/api/v1/tasks", json={"stock_code": "000001.SZ"})
    resp = await client.get("/api/v1/tasks")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


async def test_cancel_task(client):
    resp = await client.post("/api/v1/tasks", json={"stock_code": "000001.SZ"})
    task_id = resp.json()["task_id"]
    resp2 = await client.delete(f"/api/v1/tasks/{task_id}")
    assert resp2.status_code == 200


async def test_invalid_stock_code(client):
    resp = await client.post("/api/v1/tasks", json={"stock_code": "INVALID_CODE_TOO_LONG_XXX"})
    assert resp.status_code == 422


async def test_health_endpoint(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
