import copy

import pytest
import yaml

from avd_services.checks import check_vni_ranges, run_checks
from avd_services.inventory import hosts_by_dc, load_inventory, load_yaml


def _run(repo, mutate_services=None, mutate_ranges=None):
    services_file = repo / "group_vars" / "LISLE_SERVICES.yml"
    if mutate_services:
        services = load_yaml(services_file)
        mutate_services(services["tenants"])
        services_file.write_text(yaml.safe_dump(services))
    ranges = load_yaml(repo / "vni_ranges.yml")
    if mutate_ranges:
        mutate_ranges(ranges["vni_ranges"])
    hostvars, _ = load_inventory(repo)
    findings = run_checks(hostvars, hosts_by_dc(hostvars), ranges["vni_ranges"], ranges["vni_default_customer_type"])
    return [f.message for f in findings if f.level == "error"]


def _svi(tenants, index=0):
    return tenants[0]["vrfs"][0]["svis"][index]


def test_fixture_is_clean(fixture_repo):
    assert _run(fixture_repo) == []


@pytest.mark.parametrize("mutate, expected", [
    (lambda t: _svi(t).update(vni_override=30100), "VNI 30100 is outside lisle external range"),
    (lambda t: _svi(t).update(vni_override=40100), "VNI 40100 is in omaha/external's range"),
    (lambda t: _svi(t).update(tags=["PAIR9"]), "tag 'PAIR9' is not a leaf pair"),
    (lambda t: _svi(t).update(tags=[]), "tags must list the leaf pairs"),
    (lambda t: _svi(t).update(ip_address_virtual_secondaries=["10.1.1.1/24"]), "key 'ip_address_virtual_secondaries' is not allowed"),
    (lambda t: _svi(t).update(ip_helpers=[{"ip_helper": "10.9.9.9"}]), "key 'ip_helpers' is not allowed"),
    (lambda t: _svi(t).update(mtu=9216), "mtu 9216 must be 68-9214"),
    (lambda t: _svi(t).pop("enabled"), "SVI must set enabled: true"),
    (lambda t: _svi(t).pop("ip_address_virtual"), "SVI needs ip_address_virtual"),
    (lambda t: _svi(t).update(ip_address_virtual="10.100.0.0/24"), "network or broadcast address"),
    (lambda t: _svi(t).update(ip_address_virtual="10.100.0.1"), "needs a prefix length"),
    (lambda t: _svi(t).pop("vni_override"), "vni_override is required"),
    (lambda t: _svi(t).update(rd_override="10.0.0.1:100"), "is a full RD"),
    (lambda t: _svi(t).update(vni_override=20300), "VNI used by more than one VLAN"),
    (lambda t: t[0]["vrfs"][0].update(vrf_vni=20100), "collides with VRF PROD's L3 VNI"),
    (lambda t: t[0]["vrfs"][0].pop("vrf_vni"), "VRF PROD needs vrf_vni"),
    (lambda t: t[0]["vrfs"][0].update(route_target={"import": []}), "VRFs already exist and are not rendered"),
    (lambda t: _svi(t).update(id=4094), "VLAN id 4094 must be 2-4092"),
    (lambda t: _svi(t)["_migration"].update(gateway_mode="centralised"), "gateway_mode 'centralised'"),
    (lambda t: _svi(t)["_migration"].update(customer_type="shared"), "customer_type 'shared'"),
    (lambda t: t[0]["vrfs"][0].update(name="default"), "SVI in VRF default: its subnet would not be advertised"),
    # External gateway VLAN 300 must sit on the border pair (PAIR1) unless overridden.
    (lambda t: t[0]["l2vlans"][1].update(tags=["PAIR2"]), "external gateway VLAN 300 is not on PAIR1"),
    (lambda t: t[0]["l2vlans"][1]["_migration"].update(external_gateway_pair="PAIR9"), "external_gateway_pair 'PAIR9' is not a leaf pair"),
    (lambda t: t[0]["l2vlans"][1]["_migration"].update(gateway_mode="distributed_anycast"), "l2vlan gateway_mode 'distributed_anycast'"),
    # Centralized anycast pairing rules (VLAN 200: SVI on PAIR2, l2vlan on PAIR1).
    (lambda t: _svi(t, 1)["_migration"].update(gateway_mode="distributed_anycast"), "only for centralized_anycast"),
    (lambda t: t[0]["l2vlans"][0].update(tags=["PAIR1", "PAIR2"]), "get both the SVI and the l2vlan"),
    (lambda t: t[0]["l2vlans"][0].update(vni_override=10201), "same vni_override"),
    (lambda t: t[0]["l2vlans"].append(copy.deepcopy(t[0]["l2vlans"][1])), "VLAN defined 2 times"),
])
def test_rule_violations(fixture_repo, mutate, expected):
    errors = _run(fixture_repo, mutate_services=mutate)
    assert any(expected in message for message in errors), errors


def _warnings(repo, mutate):
    services_file = repo / "group_vars" / "LISLE_SERVICES.yml"
    services = load_yaml(services_file)
    mutate(services["tenants"])
    services_file.write_text(yaml.safe_dump(services))
    ranges = load_yaml(repo / "vni_ranges.yml")
    hostvars, _ = load_inventory(repo)
    findings = run_checks(hostvars, hosts_by_dc(hostvars), ranges["vni_ranges"], ranges["vni_default_customer_type"])
    return [f.message for f in findings if f.level == "warning"]


def test_fixture_has_no_warnings(fixture_repo):
    assert _warnings(fixture_repo, lambda t: None) == []


@pytest.mark.parametrize("mutate, expected", [
    (lambda t: _svi(t).update(rt_override="50001:100"), "rt_override '50001:100' is not the fabric scheme (20100:100)"),
    (lambda t: _svi(t).update(rd_override=1100), "rd_override 1100 is not the fabric scheme (<router-id>:100)"),
])
def test_scheme_warnings(fixture_repo, mutate, expected):
    assert any(expected in message for message in _warnings(fixture_repo, mutate))


def test_vni_ranges_overlap_and_malformed():
    findings = check_vni_ranges({
        "lisle": {"internal": {"start": 100, "end": 200}, "external": {"start": 150, "end": 300}},
        "omaha": {"internal": {"start": 500, "end": 400}, "external": None},
    })
    messages = [(f.level, f.message) for f in findings]
    assert ("error", "ranges lisle/internal and lisle/external overlap") in messages
    assert any(level == "error" and "malformed" in message for level, message in messages)
    assert any(level == "warning" and "not set" in message for level, message in messages)


def test_external_gateway_override_moves_the_vlan(fixture_repo):
    def override(tenants):
        tenants[0]["l2vlans"][1].update(tags=["PAIR2"])
        tenants[0]["l2vlans"][1]["_migration"]["external_gateway_pair"] = "PAIR2"
    assert _run(fixture_repo, mutate_services=override) == []


def test_fabric_facts_required(fixture_repo):
    path = fixture_repo / "group_vars" / "LISLE_FABRIC.yml"
    fabric = load_yaml(path)
    del fabric["l3leaf"]["node_groups"][0]["nodes"][0]["loopback_ipv4_address"]
    fabric["l3leaf"]["node_groups"][1]["nodes"].pop()
    path.write_text(yaml.safe_dump(fabric))
    errors = _run(fixture_repo)
    assert any("loopback_ipv4_address is required" in e for e in errors)
    assert any("leaf is not in any l3leaf node_group" in e for e in errors)
