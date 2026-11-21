def verify(demo):
    profile = demo.request(f"/api/v1/players/{demo.player}")
    lobby = demo.request(f"/api/v1/lobbies/{demo.lobby}")
    assert profile["player_id"] == demo.player and profile["level"] >= 1
    assert lobby["lobby_id"] == demo.lobby and lobby["base_id"] == demo.base
    assert any(member["player_id"] == demo.player for member in lobby["players"])
    print("PASS Player/Game: fresh authenticated profile and a persistent lobby with the correct member and base.")
