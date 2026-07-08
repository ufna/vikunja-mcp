import json

import httpx

from tests.unit.test_api import make_api


def test_get_or_create_label_reuses_case_insensitively():
    """Vikunja лейблы принадлежат создателю, а GET /labels отдаёт и те, что засвечены
    на доступных задачах (не только свои). Агент, набравший "Bug"/"bug ", НЕ должен
    форкать дубль поверх canonical "bug" — матч по имени регистро- и пробел-независимый.
    Реальный инцидент 2026-07-08: бот создал второй бесцветный `bug`."""
    created = []

    def handler(request):
        if request.method == "GET" and request.url.path.endswith("/labels"):
            return httpx.Response(200, json=[{"id": 1, "title": "bug", "hex_color": "d73a4a"}])
        if request.method == "PUT" and request.url.path.endswith("/labels"):
            created.append(json.loads(request.content))
            return httpx.Response(201, json={"id": 99, "title": "bug"})
        return httpx.Response(404, json={"message": "unexpected"})

    api = make_api(handler)
    for variant in ("bug", "Bug", "BUG", "  bug "):
        assert api.get_or_create_label(variant)["id"] == 1
    assert created == []  # ни одного PUT /labels — существующий переиспользован


def test_get_or_create_label_creates_when_absent():
    """Нет совпадения по имени — тогда честно создаём (единственный легитимный PUT)."""
    created = []

    def handler(request):
        if request.method == "GET" and request.url.path.endswith("/labels"):
            return httpx.Response(200, json=[{"id": 1, "title": "bug"}])
        if request.method == "PUT" and request.url.path.endswith("/labels"):
            created.append(json.loads(request.content))
            return httpx.Response(201, json={"id": 7, "title": "epic"})
        return httpx.Response(404, json={"message": "unexpected"})

    api = make_api(handler)
    assert api.get_or_create_label("epic")["id"] == 7
    assert created == [{"title": "epic"}]
