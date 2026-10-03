from modules.entity_ids import is_creature_id


def test_creature_prefix_both_styles():
    assert is_creature_id("CRE-0001")
    assert is_creature_id("cre_bishuishou")
    assert is_creature_id("cre-aobing")
    assert is_creature_id("Cre_x")


def test_non_creatures():
    assert not is_creature_id("CHAR-0007")
    assert not is_creature_id("creature_x")
    assert not is_creature_id("crew")
    assert not is_creature_id(None)
    assert not is_creature_id(12)
