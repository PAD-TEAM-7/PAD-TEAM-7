def verify(demo):
    base = demo.request(f"/api/v1/bases/{demo.base}")
    assert base["lobby_id"] == demo.lobby and base["barricade_count"] > 0
    body = {"player_id": demo.player, "lobby_id": demo.lobby, "recipe_id": "recipe-barricade-kit"}
    crafted = demo.request("/api/v1/crafts", method="POST", body=body, expected=201)
    job = demo.request(f"/api/v1/crafts/{crafted['job_id']}")
    inventory = demo.request(f"/api/v1/players/{demo.player}/inventory")
    assert job["status"] == "completed"
    assert any(item["item_id"] == "barricade-kit-01" and item["count"] == 1
               for item in inventory["items"])
    print("PASS Base/Crafting: persisted barricade, completed craft job, and delivered inventory reward.")
