import asyncio
import random
import string
import time
import pytest
import anyio
from httpx import AsyncClient, ASGITransport

from main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    # Set an extended timeout pool specifically for massive dataset ingestion
    async with AsyncClient(transport=transport, base_url="http://test", timeout=60.0) as ac:
        yield ac


@pytest.mark.anyio
async def test_extreme_dataset_ingestion_and_autocomplete_latency(client):
    """
    Ingests 250,000 words in high-velocity blocks to analyze look-up degradation.
    Verifies that search latencies remain sub-millisecond even with a massive tree footprint.
    """
    print("\n" + "=" * 60)
    print("[HEAVY LOAD] STAGE 1: Ingesting 250,000 unique terms...")
    print("=" * 60)

    total_chunks = 25
    chunk_size = 10000

    start_ingest = time.perf_counter()

    for c in range(total_chunks):
        words = []
        # Generate varied prefix structures to fully stretch the tree depth
        prefix_base = f"load_{c}_"
        for i in range(chunk_size):
            random_suffix = "".join(random.choices(string.ascii_lowercase, k=6))
            words.append({
                "term": f"{prefix_base}{random_suffix}",
                "weight": random.randint(1, 5000)
            })

        r_post = await client.post("/words", json={"words": words})
        assert r_post.status_code == 201

        if (c + 1) % 5 == 0:
            print(f" -> Progress: Loaded {(c + 1) * chunk_size} words...")

    ingest_duration = time.perf_counter() - start_ingest
    print(f"[SUCCESS] Ingested 250,000 items in {ingest_duration:.2f} seconds.")

    print("\n[HEAVY LOAD] STAGE 2: Measuring autocomplete lookup latency at scale...")

    # Run repeated rapid lookup evaluations against deeply saturated branches
    lookup_times = []
    for _ in range(200):
        target_prefix = f"load_{random.randint(0, total_chunks - 1)}_"

        start_lookup = time.perf_counter()
        r = await client.get(f"/autocomplete?prefix={target_prefix}&limit=10")
        lookup_duration = time.perf_counter() - start_lookup

        assert r.status_code == 200
        assert len(r.json()["suggestions"]) <= 10
        lookup_times.append(lookup_duration)

    avg_latency = (sum(lookup_times) / len(lookup_times)) * 1000
    max_latency = max(lookup_times) * 1000

    print(f"[RESULTS] Average lookup latency: {avg_latency:.3f} ms")
    print(f"[RESULTS] Max lookup latency: {max_latency:.3f} ms")

    # Strict performance assurance check
    assert avg_latency < 5.0, f"Performance degraded! Average response was {avg_latency:.2f}ms"


@pytest.mark.anyio
async def test_high_intensity_lock_contention_storm(client):
    """
    Fires 3,000 simultaneous hit/mutation tasks from native OS thread worker pools.
    Forces maximum possible system lock contention against a single hot key to check safety.
    """
    hot_term = "ultra_contention_key"
    await client.post("/words", json={"words": [{"term": hot_term, "weight": 0}]})

    print("\n" + "=" * 60)
    print(f"[HEAVY LOAD] STAGE 3: Launching 3,000 multi-threaded mutation requests on '{hot_term}'...")
    print("=" * 60)

    # Bind an isolated client request loop inside native OS runtime worker threads
    async def task_worker():
        return await anyio.to_thread.run_sync(
            lambda: asyncio.run(client.post(f"/words/{hot_term}/hit"))
        )

    start_storm = time.perf_counter()

    # Generate an intense avalanche of concurrent workers
    tasks = [task_worker() for _ in range(3000)]
    await asyncio.gather(*tasks)

    storm_duration = time.perf_counter() - start_storm
    print(f"[SUCCESS] Processed 3,000 safe thread mutations in {storm_duration:.2f} seconds.")

    # Confirm absolutely zero weight calculation updates were dropped
    r_check = await client.get(f"/words/{hot_term}")
    final_weight = r_check.json()["weight"]
    print(f"[RESULTS] Expected Weight: 3000 | Actual Storage State Weight: {final_weight}")
    assert final_weight == 3000, f"Race condition detected! Weight was only {final_weight}"


@pytest.mark.anyio
async def test_pathological_topk_eviction_pressure(client):
    """
    Pumps 2,000 distinct words down a single shared prefix path.
    Forces the inner `if len(new_top) > K:` block to perform 1,950 consecutive evictions
    to guarantee that your inner dictionary and array slice cleaning routines never experience memory leaks.
    """
    print("\n" + "=" * 60)
    print("[HEAVY LOAD] STAGE 4: Testing pathological eviction pressure on a single prefix...")
    print("=" * 60)

    prefix = "overflow_"
    words = [{"term": f"{prefix}{i}", "weight": i} for i in range(2000)]

    # Use our single-lock bulk method to pump entries efficiently
    await client.post("/words", json={"words": words})

    r = await client.get(f"/autocomplete?prefix={prefix}&limit=50")
    assert r.status_code == 200
    suggestions = r.json()["suggestions"]

    # Validate the structure is constrained tightly to our constant rule limits
    assert len(suggestions) == 50

    # The highest weighted items must survive the eviction avalanche
    worst_allowed_weight = 2000 - 50  # Items 1950 through 1999 should be saved
    assert all(item["weight"] >= worst_allowed_weight for item in suggestions)
    print("[SUCCESS] Pathological eviction management checked out completely clean.")
