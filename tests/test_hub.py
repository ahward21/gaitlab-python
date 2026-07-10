from gaitlab.hub import DataHub


def test_hub_publish_get_case_insensitive():
    hub = DataHub()
    hub.publish("physio.Heart_Rate_bpm", 120.0, "bpm")
    assert hub.get_or_default("PHYSIO.heart_rate_bpm") == 120.0
    assert hub.get_unit("physio.heart_rate_bpm") == "bpm"
    assert hub.channel_ids() == ["physio.Heart_Rate_bpm"]


def test_hub_listeners():
    hub = DataHub()
    seen: list[tuple[str, float]] = []
    hub.subscribe(lambda cid, v: seen.append((cid, v)))
    hub.publish("a", 1.0)
    assert seen == [("a", 1.0)]
