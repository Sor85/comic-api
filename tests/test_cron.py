from datetime import datetime

import pytest

from src.services import cron
from src.services.jm_sync import SyncSettings


def test_next_runs_follow_fields_and_vixie_day_rule():
    after = datetime(2026, 10, 17, 1, 34, 20)  # Saturday
    assert cron.next_after("*/5 * * * *", after) == datetime(2026, 10, 17, 1, 35)
    assert cron.next_after("30 3 * * 1-5", after) == datetime(2026, 10, 19, 3, 30)
    assert cron.next_after("0 9 1 * *", after) == datetime(2026, 11, 1, 9, 0)
    assert cron.next_after("0 0 * * 7", after) == datetime(2026, 10, 18, 0, 0)
    assert cron.next_after("0 0 * dec sun", after) == datetime(2026, 12, 6, 0, 0)
    # Both day fields restricted: either one matches.
    assert cron.next_after("0 12 20 * mon", after) == datetime(2026, 10, 19, 12, 0)
    assert cron.next_after("0 0 29 2 *", after) == datetime(2028, 2, 29, 0, 0)


@pytest.mark.parametrize("expression, message", [
    ("* * * * *", "至少间隔 5 分钟"), ("0-4 3 * * *", "至少间隔 5 分钟"), ("0 0 31 2 *", "5 年内不会运行"),
    ("61 * * * *", "超出 0–59"), ("0 0 * *", "5 个字段"), ("5-1 * * * *", "范围或步长无效"), ("*/0 * * * *", "范围或步长无效"),
])
def test_invalid_schedules_are_rejected(expression, message):
    with pytest.raises(cron.CronError, match=message):
        cron.validate(expression)


def test_descriptions_and_legacy_intervals():
    assert cron.describe("0 * * * *") == "每小时整点"
    assert cron.describe("15 */6 * * *") == "每 6 小时的第 15 分"
    assert cron.describe("30 3 * * *") == "每天 03:30"
    assert cron.describe("0 2 * * 1-5") == "每个工作日 02:00"
    assert cron.describe("0 8 * * 1,3,0") == "每周一、三、日 08:00"
    assert cron.describe("0 4 1 * *") == "每月 1 日 04:00"
    assert cron.describe("0 4 1,15 * *") == "自定义：0 4 1,15 * *"
    assert [cron.from_interval(m) for m in (5, 60, 120, 1440, 10080)] == [
        "*/5 * * * *", "0 * * * *", "0 */2 * * *", "0 0 * * *", "0 0 * * 0"]


def test_settings_normalize_cron_and_preview_api(tmp_path):
    from fastapi.testclient import TestClient
    from main import create_app
    from src.storage import Store
    from tests.fakes import DemoPlugin

    assert SyncSettings(cron="  30   3 * * *").cron == "30 3 * * *"
    with pytest.raises(ValueError):
        SyncSettings(cron="* * * * *")
    store = Store(str(tmp_path))
    with TestClient(create_app(store, {"demo": DemoPlugin(store)})) as client:
        data = client.get("/api/cron/preview", params={"expression": "0 3 * * *"}).json()
        assert data["expression"] == "0 3 * * *" and data["description"] == "每天 03:00" and len(data["runs"]) == 5
        assert all(run["label"].endswith("03:00") for run in data["runs"])
        error = client.get("/api/cron/preview", params={"expression": "* * * * *"})
        assert error.status_code == 422 and "5 分钟" in error.json()["error"]["message"]
