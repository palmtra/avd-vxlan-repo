from avd_services.inventory import hosts_by_dc, load_inventory


def test_hostvars_merge_all_then_groups(fixture_repo):
    hostvars, groups = load_inventory(fixture_repo)
    assert sorted(hostvars) == ["leaf1a", "leaf1b", "leaf2a", "leaf2b"]
    leaf = hostvars["leaf1a"]
    assert leaf["cv_server"] == "cv.example.invalid"  # group_vars/all.yml
    assert leaf["data_center"] == "lisle"  # LISLE_FABRIC
    assert leaf["tenants"][0]["name"] == "LISLE_MIGRATION"  # LISLE_SERVICES
    # LISLE_LEAVES is declared twice (top level and as a child); both count.
    assert groups["leaf1a"][0] == "all"
    assert {"LISLE", "LISLE_FABRIC", "LISLE_SERVICES", "LISLE_LEAVES"} <= set(groups["leaf1a"])


def test_host_vars_win(fixture_repo):
    (fixture_repo / "host_vars").mkdir()
    (fixture_repo / "host_vars" / "leaf1a.yml").write_text("serial_number: JPE123\ndata_center: lisle\n")
    hostvars, _ = load_inventory(fixture_repo)
    assert hostvars["leaf1a"]["serial_number"] == "JPE123"
    assert "serial_number" not in hostvars["leaf1b"]


def test_hosts_by_dc(fixture_repo):
    hostvars, _ = load_inventory(fixture_repo)
    assert hosts_by_dc(hostvars) == {"lisle": ["leaf1a", "leaf1b", "leaf2a", "leaf2b"]}
