import pytest

from avd_services.__main__ import main
from avd_services.render import slice_services


def test_slice_keeps_only_service_keys():
    full = {
        "hostname": "leaf1a",
        "vlans": [{"id": 100, "name": "WEB"}, {"id": 4094, "name": "MLAG"}],
        "vlan_interfaces": [{"name": "Vlan100"}, {"name": "Vlan4094"}, {"name": "Vlan300"}],
        "vxlan_interface": {"vxlan1": {"description": "x", "vxlan": {
            "source_interface": "Loopback1", "vlans": [{"id": 100, "vni": 1}, {"id": 999, "vni": 2}], "vrfs": [{"name": "PROD"}],
        }}},
        "router_bgp": {"as": "65101", "router_id": "1.1.1.1", "neighbors": [{}], "vlans": [{"id": 100}, {"id": 999}], "vrfs": [{}]},
        "mlag_configuration": {}, "loopback_interfaces": [{}], "vrfs": [{}],
    }
    assert slice_services(full, {100, 300}, {100}) == {
        "vlans": [{"id": 100, "name": "WEB"}],
        "vlan_interfaces": [{"name": "Vlan100"}],
        "vxlan_interface": {"vxlan1": {"vxlan": {"vlans": [{"id": 100, "vni": 1}]}}},
        "router_bgp": {"as": "65101", "vlans": [{"id": 100}]},
    }


def test_slice_of_nothing_is_empty():
    assert slice_services({"vlans": [{"id": 4094}], "router_bgp": {"as": "1"}}, set(), set()) == {}


@pytest.fixture
def built(fixture_repo):
    assert main(["--root", str(fixture_repo), "build"]) == 0
    configs = fixture_repo / "intended" / "configs"
    return {path.stem: path.read_text() for path in configs.glob("*.cfg")}


def test_build_renders_every_leaf(built):
    assert sorted(built) == ["leaf1a", "leaf1b", "leaf2a", "leaf2b"]


def test_distributed_svi_on_every_pair(built):
    for leaf in built.values():
        assert "interface Vlan100\n" in leaf
        assert "ip address virtual 10.100.0.1/24" in leaf
        assert "vxlan vlan 100 vni 20100" in leaf


def test_rd_rt_overrides(built):
    assert "rd 10.250.0.1:100\n      route-target both 50001:100" in built["leaf1a"]
    assert "rd 10.250.0.4:100\n" in built["leaf2b"]
    # No override: AVD's <router-id>:<vni> and <vni>:<vni>.
    assert "rd 10.250.0.1:20300\n      route-target both 20300:20300" in built["leaf1a"]


def test_centralized_gateway_only_on_gateway_pair(built):
    for leaf in ("leaf2a", "leaf2b"):
        assert "interface Vlan200\n" in built[leaf]
        assert "ip address virtual 10.200.0.1/24" in built[leaf]
    for leaf in ("leaf1a", "leaf1b"):
        assert "interface Vlan200" not in built[leaf]
        assert "vlan 200\n   name APP" in built[leaf]
        assert "vxlan vlan 200 vni 10200" in built[leaf]


def test_l2_only_vlan_on_tagged_pair_only(built):
    assert "vxlan vlan 300 vni 20300" in built["leaf1a"]
    assert "interface Vlan300" not in built["leaf1a"]
    assert "vlan 300" not in built["leaf2a"]


def test_no_fabric_config_in_configlets(built):
    forbidden = ("Loopback", "mlag", "MLAG", "router-id", "vrf instance", "neighbor", "vxlan source-interface",
                 "vxlan vrf", "ip virtual-router mac-address", "hostname", "Port-Channel", "Ethernet")
    for name, leaf in built.items():
        for text in forbidden:
            assert text not in leaf, "%s renders %r" % (name, text)
    assert "router bgp 65101\n" in built["leaf1a"]
    assert "router bgp 65102\n" in built["leaf2a"]


def test_placeholder_banner(fixture_repo):
    path = fixture_repo / "group_vars" / "LISLE_FABRIC.yml"
    path.write_text(path.read_text().replace("_fabric_facts_verified: true", "_fabric_facts_verified: false"))
    assert main(["--root", str(fixture_repo), "build"]) == 0
    assert "PLACEHOLDER FABRIC FACTS" in (fixture_repo / "intended" / "configs" / "leaf1a.cfg").read_text()


def test_build_fails_on_rule_violation(fixture_repo, capsys):
    path = fixture_repo / "group_vars" / "LISLE_SERVICES.yml"
    path.write_text(path.read_text().replace("tags: [PAIR1, PAIR2]", "tags: [PAIR9]"))
    assert main(["--root", str(fixture_repo), "build"]) == 1
    assert "tag 'PAIR9'" in capsys.readouterr().out
    assert not (fixture_repo / "intended").exists()


def test_build_removes_stale_configs(fixture_repo):
    configs = fixture_repo / "intended" / "configs"
    configs.mkdir(parents=True)
    (configs / "retired-leaf.cfg").write_text("vlan 1\n")
    assert main(["--root", str(fixture_repo), "build"]) == 0
    assert not (configs / "retired-leaf.cfg").exists()
