from agent_management.union_find import UnionFind


def test_single_element():
    uf = UnionFind()
    uf.add("a")
    assert uf.find("a") == "a"


def test_union_two_elements():
    uf = UnionFind()
    uf.union("a", "b")
    assert uf.find("a") == uf.find("b")


def test_transitive_union():
    uf = UnionFind()
    uf.union("a", "b")
    uf.union("b", "c")
    assert uf.find("a") == uf.find("b") == uf.find("c")


def test_separate_groups():
    uf = UnionFind()
    uf.union("a", "b")
    uf.union("c", "d")
    assert uf.find("a") == uf.find("b")
    assert uf.find("c") == uf.find("d")
    assert uf.find("a") != uf.find("c")


def test_get_groups_returns_all():
    uf = UnionFind()
    uf.union("a", "b")
    uf.union("b", "c")
    uf.add("d")
    groups = uf.get_groups()
    assert len(groups) == 2  # {a,b,c} and {d}

    members = sorted([sorted(g) for g in groups])
    assert members == [["a", "b", "c"], ["d"]]


def test_get_groups_single_element_not_included():
    """Groups with only 1 member are not merge candidates — skip them."""
    uf = UnionFind()
    uf.union("a", "b")
    uf.add("lone")
    groups = uf.get_merge_groups()  # only groups with 2+ members
    assert len(groups) == 1
    assert sorted(groups[0]) == ["a", "b"]
