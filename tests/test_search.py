async def _write(client, user_id, text):
    r = await client.post("/memories", json={"text": text, "user_id": user_id, "source": "test"})
    assert r.status_code == 201
    return r.json()


async def test_write_then_relevant_query_returns_it(client, user_id):
    target = await _write(client, user_id, "My favorite programming language is Python")
    await _write(client, user_id, "I am allergic to peanuts")
    await _write(client, user_id, "The capital of France is Paris")

    r = await client.get("/memories/search", params={"query": "which language do I code in?", "user_id": user_id})
    assert r.status_code == 200
    results = r.json()
    assert results[0]["id"] == target["id"]
    assert 0.0 <= results[0]["score"] <= 1.0
    assert [x["score"] for x in results] == sorted((x["score"] for x in results), reverse=True)


async def test_top_k_limits_results(client, user_id):
    for i in range(4):
        await _write(client, user_id, f"fact number {i}")
    r = await client.get("/memories/search", params={"query": "fact", "user_id": user_id, "top_k": 2})
    assert len(r.json()) == 2


async def test_search_never_crosses_users(client, user_id):
    other = f"{user_id}-other"
    await _write(client, other, "My secret PIN is 4921")
    r = await client.get("/memories/search", params={"query": "secret PIN", "user_id": user_id})
    assert r.status_code == 200
    assert r.json() == []
