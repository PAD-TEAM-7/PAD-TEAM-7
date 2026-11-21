def verify(demo):
    zombie, _ = demo.client.call("zombie-service", f"/api/v1/zombies/{demo.values['zombieId']}",
                                 source="game-service")
    balances = demo.request(f"/api/v1/players/{demo.player}/resources?lobby_id={demo.lobby}")
    assert zombie["zombie_id"] == demo.values["zombieId"] and zombie["lobby_id"] == demo.lobby
    assert any(row["item_id"] == "wood-01" and row["amount"] > 0 for row in balances["resources"])
    print("PASS Zombie/Resource: Broker reads a persisted zombie and Gateway reads real resource balances.")
