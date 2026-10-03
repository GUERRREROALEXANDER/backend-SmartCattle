def test_animals_are_empty(client):
    response = client.get("/api/animals")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}
