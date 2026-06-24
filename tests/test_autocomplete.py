import asyncio
import random
import string
from urllib.parse import quote
import pytest
import anyio
from httpx import AsyncClient, ASGITransport

from main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "healthy"}


@pytest.mark.anyio
async def test_upsert_and_get(client):
    r = await client.post("/words", json={
        "words": [
            {"term": "u1_Apple", "weight": 10},
            {"term": "u1_apricot", "weight": 3}
        ]
    })
    assert r.status_code == 201
    assert r.json()["count"] >= 2

    r = await client.get("/words/u1_apple")
    assert r.status_code == 200
    assert r.json()["term"] == "u1_Apple"
    assert r.json()["weight"] == 10


@pytest.mark.anyio
async def test_upsert_replace(client):
    await client.post("/words", json={"words": [{"term": "u2_apple", "weight": 10}]})
    await client.post("/words", json={"words": [{"term": "u2_Apple", "weight": 5}]})

    r = await client.get("/words/u2_apple")
    assert r.json()["weight"] == 5


@pytest.mark.anyio
async def test_hit_increments(client):
    await client.post("/words", json={"words": [{"term": "h1_apple", "weight": 1}]})
    await client.post("/words/h1_apple/hit")
    await client.post("/words/h1_apple/hit")

    r = await client.get("/words/h1_apple")
    assert r.json()["weight"] == 3


@pytest.mark.anyio
async def test_hit_creates(client):
    r = await client.post("/words/h2_banana/hit")
    assert r.status_code == 200
    assert r.json()["weight"] == 1


@pytest.mark.anyio
async def test_delete(client):
    await client.post("/words", json={"words": [{"term": "d1_apple", "weight": 1}]})

    r = await client.delete("/words/d1_apple")
    assert r.status_code == 204

    r = await client.get("/words/d1_apple")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_autocomplete_ranking(client):
    await client.post("/words", json={
        "words": [
            {"term": "rank_apple", "weight": 1},
            {"term": "rank_apricot", "weight": 10},
            {"term": "rank_application", "weight": 10},
            {"term": "rank_apply", "weight": 5}
        ]
    })

    r = await client.get("/autocomplete?prefix=rank_ap&limit=10")
    data = r.json()["suggestions"]
    terms = [x["term"].lower() for x in data]
    assert terms == ["rank_application", "rank_apricot", "rank_apply", "rank_apple"]


@pytest.mark.anyio
async def test_prefix_filtering(client):
    await client.post("/words", json={
        "words": [
            {"term": "filt_apple", "weight": 10},
            {"term": "filt_banana", "weight": 10},
            {"term": "filt_apartment", "weight": 10},
        ]
    })

    r = await client.get("/autocomplete?prefix=filt_ap")
    data = r.json()["suggestions"]

    assert len(data) == 2
    assert all(x["term"].lower().startswith("filt_ap") for x in data)


@pytest.mark.anyio
async def test_limit(client):
    await client.post("/words", json={
        "words": [{"term": f"lim_a{i}", "weight": i} for i in range(20)]
    })

    r = await client.get("/autocomplete?prefix=lim_a&limit=5")
    assert len(r.json()["suggestions"]) == 5


@pytest.mark.anyio
async def test_unicode_normalization(client):
    await client.post("/words", json={"words": [{"term": "uni_Café", "weight": 1}]})

    decomposed_path = quote("uni_cafe\u0301")
    r = await client.get(f"/words/{decomposed_path}")

    assert r.status_code == 200
    assert r.json()["weight"] == 1


@pytest.mark.anyio
async def test_validation_bounds(client):
    r_empty = await client.get("/autocomplete?prefix=")
    assert r_empty.status_code == 422

    r_high_limit = await client.get("/autocomplete?prefix=a&limit=51")
    assert r_high_limit.status_code == 422


@pytest.mark.anyio
async def test_bulk_autocomplete_stress(client):
    words = []
    for i in range(2000):
        w = "stress_" + "".join(random.choices(string.ascii_lowercase, k=8))
        words.append({"term": w, "weight": random.randint(0, 100)})

    r_post = await client.post("/words", json={"words": words})
    assert r_post.status_code == 201

    r = await client.get("/autocomplete?prefix=stress_&limit=10")
    assert r.status_code == 200
    assert len(r.json()["suggestions"]) <= 10


@pytest.mark.anyio
async def test_hit_concurrency(client):
    term = "race_apple"
    await client.post("/words", json={"words": [{"term": term, "weight": 0}]})

    async def send_hit():
        return await anyio.to_thread.run_sync(
            lambda: asyncio.run(client.post(f"/words/{term}/hit"))
        )

    tasks = [send_hit() for _ in range(50)]
    await asyncio.gather(*tasks)

    r = await client.get(f"/words/{term}")
    assert r.json()["weight"] == 50


@pytest.mark.anyio
async def test_delete_storm(client):
    await client.post("/words", json={
        "words": [{"term": f"storm_x{i}", "weight": i} for i in range(100)]
    })

    delete_tasks = [client.delete(f"/words/storm_x{i}") for i in range(100)]
    await asyncio.gather(*delete_tasks)

    r = await client.get("/autocomplete?prefix=storm_x")
    assert r.json()["suggestions"] == []


@pytest.mark.anyio
async def test_eviction_and_cleanup_beyond_k(client):
    prefix = "evict_"
    words = []
    for i in range(55):
        words.append({"term": f"{prefix}{i}", "weight": i + 1})

    r_post = await client.post("/words", json={"words": words})
    assert r_post.status_code == 201

    r_auto = await client.get(f"/autocomplete?prefix={prefix}&limit=50")
    assert r_auto.status_code == 200

    suggestions = r_auto.json()["suggestions"]
    assert len(suggestions) == 50

    terms_in_top = {x["term"] for x in suggestions}
    assert f"{prefix}0" not in terms_in_top
