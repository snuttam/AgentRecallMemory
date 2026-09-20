async def _ids(client, user_id, query):
    r = await client.get("/memories/search", params={"query": query, "user_id": user_id})
    return [m["id"] for m in r.json()]


async def test_delete_removes_memory(client, user_id):
    m = (await client.post("/memories", json={"text": "I drink oat milk", "user_id": user_id, "source": "t"})).json()
    r = await client.delete(f"/memories/{m['id']}", params={"user_id": user_id})
    assert r.status_code == 204
    assert m["id"] not in await _ids(client, user_id, "milk")


async def test_delete_is_idempotent(client, user_id):
    m = (await client.post("/memories", json={"text": "temp", "user_id": user_id, "source": "t"})).json()
    for _ in range(2):
        assert (await client.delete(f"/memories/{m['id']}", params={"user_id": user_id})).status_code == 204
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await client.delete(f"/memories/{missing}", params={"user_id": user_id})).status_code == 204


async def test_user_cannot_delete_another_users_memory(client, user_id):
    other = f"{user_id}-other"
    m = (await client.post("/memories", json={"text": "I own a red bike", "user_id": other, "source": "t"})).json()
    r = await client.delete(f"/memories/{m['id']}", params={"user_id": user_id})
    assert r.status_code == 204  # no existence leak
    assert m["id"] in await _ids(client, other, "bike")
