async def test_write_returns_created_memory(client, user_id):
    r = await client.post(
        "/memories",
        json={"text": "I love hiking in the Alps", "user_id": user_id, "source": "chat", "importance_hint": 0.9},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["user_id"] == user_id
    assert body["access_count"] == 0
    assert body["importance_score"] == 0.9
    assert body["id"] and body["created_at"]
    assert "embedding" not in body


async def test_write_rejects_empty_text(client, user_id):
    r = await client.post("/memories", json={"text": "", "user_id": user_id, "source": "chat"})
    assert r.status_code == 422
