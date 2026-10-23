"""Repo rules on top of the AVD schema.

AVD validates types. These checks hold the migration's own rules: what a
services-only repo may contain, VNI ranges, leaf-pair placement, and the
gateway decisions (anycast only, no secondaries, no DHCP relay, MTU).
"""

import ipaddress
from collections import namedtuple

from .services import dc_tenants, iter_services, node_groups

Finding = namedtuple("Finding", "level dc where message")

VNI_POOLS = ("internal", "customer")
EOS_SVI_MTU_MAX = 9214
VLAN_ID_MIN = 2
VLAN_ID_MAX = 4092  # 4093/4094 are AVD's MLAG VLANs on every leaf pair.

TENANT_KEYS = {"name", "vrfs", "l2vlans"}
VRF_KEYS = {"name", "vrf_vni", "vrf_id", "description", "svis"}
SVI_KEYS = {
    "id", "name", "enabled", "description", "tags", "ip_address_virtual", "mtu",
    "vni_override", "rt_override", "rd_override", "_migration",
}
L2VLAN_KEYS = {"id", "name", "tags", "vni_override", "rt_override", "rd_override", "_migration"}
MIGRATION_KEYS = {"vni_pool", "gateway_mode", "source", "change", "notes"}
GATEWAY_MODES = {"distributed_anycast", "centralized_anycast"}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _vni_range(entry):
    if not isinstance(entry, dict):
        return None
    start, end = _int(entry.get("start")), _int(entry.get("end"))
    if start is None or end is None or start < 1 or end > 16777215 or start > end:
        return None
    return start, end


def check_vni_ranges(vni_ranges):
    findings = []
    spans = []
    for dc, pools in sorted((vni_ranges or {}).items()):
        for pool in VNI_POOLS:
            entry = (pools or {}).get(pool)
            unset = entry is None or (isinstance(entry, dict) and entry.get("start") is None and entry.get("end") is None)
            span = _vni_range(entry)
            if unset:
                findings.append(Finding("warning", dc, "vni_ranges.%s.%s" % (dc, pool), "range not set; VNIs in this pool are not range-checked"))
            elif span is None:
                findings.append(Finding("error", dc, "vni_ranges.%s.%s" % (dc, pool), "malformed range %r" % (entry,)))
            else:
                spans.append((span, "%s/%s" % (dc, pool), dc))
    for index, (span_a, name_a, dc_a) in enumerate(spans):
        for span_b, name_b, _ in spans[index + 1:]:
            if span_a[0] <= span_b[1] and span_b[0] <= span_a[1]:
                findings.append(Finding("error", dc_a, "vni_ranges", "ranges %s and %s overlap" % (name_a, name_b)))
    return findings


def check_tenants_consistent(dc, hostvars, hosts):
    first = dc_tenants(hostvars, hosts)
    return [
        Finding("error", dc, host, "leaf sees different tenants than %s; services must come from one %s_SERVICES group" % (hosts[0], dc.upper()))
        for host in hosts[1:]
        if list(hostvars[host].get("tenants") or []) != first
    ]


def check_fabric_facts(dc, hostvars, hosts):
    findings = []
    if not hosts:
        return findings
    groups = node_groups(hostvars[hosts[0]])
    placed = {node: group for group, nodes in groups.items() for node in nodes}
    for host in hosts:
        if host not in placed:
            findings.append(Finding("error", dc, host, "leaf is not in any l3leaf node_group of %s_FABRIC" % dc.upper()))
    for group in (hostvars[hosts[0]].get("l3leaf") or {}).get("node_groups") or []:
        if not str(group.get("bgp_as") or "").strip():
            findings.append(Finding("error", dc, "node_group %s" % group.get("group"), "bgp_as is required (router bgp line of the VLAN blocks)"))
        for node in group.get("nodes") or []:
            if not node.get("loopback_ipv4_address"):
                findings.append(Finding(
                    "error", dc, "node %s" % node.get("name"),
                    "loopback_ipv4_address is required: it is the router-id in each VLAN's RD and must match production",
                ))
            if node.get("name") not in hosts:
                findings.append(Finding("error", dc, "node %s" % node.get("name"), "node_group member is not in inventory.yml"))
    if not hostvars[hosts[0]].get("_fabric_facts_verified"):
        findings.append(Finding("warning", dc, "%s_FABRIC" % dc.upper(), "fabric facts are placeholders (_fabric_facts_verified: false); deploy is blocked"))
    return findings


def _unknown_keys(data, allowed):
    return sorted(str(key) for key in (data or {}) if key not in allowed)


def _check_svi_gateway(dc, where, svi):
    findings = []
    address = svi.get("ip_address_virtual")
    if not address:
        findings.append(Finding("error", dc, where, "SVI needs ip_address_virtual (anycast gateway)"))
    else:
        try:
            interface = ipaddress.ip_interface(str(address))
        except ValueError:
            findings.append(Finding("error", dc, where, "ip_address_virtual %r is not an address/prefix" % (address,)))
        else:
            if "/" not in str(address):
                findings.append(Finding("error", dc, where, "ip_address_virtual %s needs a prefix length" % address))
            elif interface.network.prefixlen < interface.max_prefixlen - 1 and interface.ip in (
                interface.network.network_address, interface.network.broadcast_address,
            ):
                findings.append(Finding("error", dc, where, "ip_address_virtual %s is the network or broadcast address" % address))
    mtu = svi.get("mtu")
    if mtu is not None and (_int(mtu) is None or not 68 <= _int(mtu) <= EOS_SVI_MTU_MAX):
        findings.append(Finding("error", dc, where, "mtu %r must be 68-%d (EOS SVI maximum)" % (mtu, EOS_SVI_MTU_MAX)))
    if svi.get("enabled") is not True:
        findings.append(Finding("error", dc, where, "SVI must set enabled: true (AVD renders a shutdown SVI otherwise)"))
    return findings


def _vni_pool(service, default_pool):
    migration = service["data"].get("_migration") or {}
    return str(migration.get("vni_pool") or default_pool or "customer").strip().lower()


def check_services(dc, hostvars, hosts, vni_ranges, default_pool):
    findings = []
    if not hosts:
        return findings
    tenants = dc_tenants(hostvars, hosts)
    groups = set(node_groups(hostvars[hosts[0]]))
    dc_ranges = (vni_ranges or {}).get(dc) or {}
    other_spans = [
        (_vni_range(entry), "%s/%s" % (other_dc, pool))
        for other_dc, pools in (vni_ranges or {}).items() if other_dc != dc
        for pool, entry in (pools or {}).items()
    ]

    for t_index, tenant in enumerate(tenants):
        for key in _unknown_keys(tenant, TENANT_KEYS):
            findings.append(Finding("error", dc, "tenants[%d]" % t_index, "key %r is not allowed in a services-only repo" % key))
        for v_index, vrf in enumerate((tenant or {}).get("vrfs") or []):
            where = "tenants[%d].vrfs[%d]" % (t_index, v_index)
            for key in _unknown_keys(vrf, VRF_KEYS):
                findings.append(Finding("error", dc, where, "key %r is not allowed; VRFs already exist and are not rendered" % key))
            if vrf.get("vrf_vni") is None and vrf.get("vrf_id") is None:
                findings.append(Finding("error", dc, where, "VRF %s needs vrf_vni (the fabric's L3 VNI)" % vrf.get("name")))

    by_vlan = {}
    by_vni = {}
    for service in iter_services(tenants):
        data, where, kind = service["data"], service["where"], service["kind"]
        allowed = SVI_KEYS if kind == "svi" else L2VLAN_KEYS
        for key in _unknown_keys(data, allowed):
            findings.append(Finding("error", dc, where, "key %r is not allowed on a migration %s" % (key, kind)))
        for key in _unknown_keys(data.get("_migration"), MIGRATION_KEYS):
            findings.append(Finding("error", dc, where, "_migration key %r is not known" % key))

        vlan_id = _int(data.get("id"))
        if vlan_id is None or not VLAN_ID_MIN <= vlan_id <= VLAN_ID_MAX:
            findings.append(Finding("error", dc, where, "VLAN id %r must be %d-%d" % (data.get("id"), VLAN_ID_MIN, VLAN_ID_MAX)))
            continue
        by_vlan.setdefault(vlan_id, []).append(service)

        tags = data.get("tags")
        if not tags:
            findings.append(Finding("error", dc, where, "tags must list the leaf pairs that carry VLAN %d" % vlan_id))
        for tag in tags or []:
            if str(tag) not in groups:
                findings.append(Finding("error", dc, where, "tag %r is not a leaf pair in %s_FABRIC (%s)" % (tag, dc.upper(), ", ".join(sorted(groups)))))

        if kind == "svi":
            findings.extend(_check_svi_gateway(dc, where, data))
            mode = (data.get("_migration") or {}).get("gateway_mode")
            if mode is not None and mode not in GATEWAY_MODES:
                findings.append(Finding("error", dc, where, "gateway_mode %r must be one of %s" % (mode, ", ".join(sorted(GATEWAY_MODES)))))

        if ":" in str(data.get("rd_override") or ""):
            findings.append(Finding(
                "error", dc, where,
                "rd_override %r is a full RD, which every leaf would share; give the assigned number only "
                "(the leaf router-id is the admin field)" % data.get("rd_override"),
            ))

        vni = _int(data.get("vni_override"))
        if vni is None or not 1 <= vni <= 16777215:
            findings.append(Finding("error", dc, where, "vni_override is required (1-16777215); the migration allocates it"))
            continue
        by_vni.setdefault(vni, set()).add(vlan_id)
        pool = _vni_pool(service, default_pool)
        if pool not in VNI_POOLS:
            findings.append(Finding("error", dc, where, "vni_pool %r must be internal or customer" % pool))
            continue
        span = _vni_range(dc_ranges.get(pool))
        if span and not span[0] <= vni <= span[1]:
            findings.append(Finding("error", dc, where, "VNI %d is outside %s %s range %d-%d" % (vni, dc, pool, span[0], span[1])))
        for other_span, name in other_spans:
            if other_span and other_span[0] <= vni <= other_span[1]:
                findings.append(Finding("error", dc, where, "VNI %d is in %s's range" % (vni, name)))

    for vni, vlan_ids in sorted(by_vni.items()):
        if len(vlan_ids) > 1:
            findings.append(Finding("error", dc, "vni %d" % vni, "VNI used by more than one VLAN: %s" % sorted(vlan_ids)))

    vrf_vnis = {
        _int(vrf.get("vrf_vni")): vrf.get("name")
        for tenant in tenants for vrf in (tenant or {}).get("vrfs") or [] if _int(vrf.get("vrf_vni")) is not None
    }
    for vni in sorted(set(by_vni) & set(vrf_vnis)):
        findings.append(Finding("error", dc, "vni %d" % vni, "L2 VNI collides with VRF %s's L3 VNI" % vrf_vnis[vni]))

    for vlan_id, entries in sorted(by_vlan.items()):
        findings.extend(_check_vlan_entries(dc, vlan_id, entries))
    return findings


def _check_vlan_entries(dc, vlan_id, entries):
    """One entry per VLAN, except centralized anycast: one SVI on the gateway
    pair plus one l2vlan, same VNI, on the other pairs."""
    if len(entries) == 1:
        return []
    where = "vlan %d" % vlan_id
    kinds = sorted(entry["kind"] for entry in entries)
    if kinds != ["l2vlan", "svi"]:
        return [Finding("error", dc, where, "VLAN defined %d times (%s)" % (len(entries), ", ".join(e["where"] for e in entries)))]
    svi = next(e["data"] for e in entries if e["kind"] == "svi")
    l2vlan = next(e["data"] for e in entries if e["kind"] == "l2vlan")
    findings = []
    if (svi.get("_migration") or {}).get("gateway_mode") != "centralized_anycast":
        findings.append(Finding("error", dc, where, "SVI plus l2vlan for one VLAN is only for centralized_anycast; set _migration.gateway_mode"))
    if _int(svi.get("vni_override")) != _int(l2vlan.get("vni_override")):
        findings.append(Finding("error", dc, where, "SVI and l2vlan must use the same vni_override"))
    if str(svi.get("name")) != str(l2vlan.get("name")):
        findings.append(Finding("error", dc, where, "SVI and l2vlan must use the same name"))
    overlap = set(map(str, svi.get("tags") or [])) & set(map(str, l2vlan.get("tags") or []))
    if overlap:
        findings.append(Finding("error", dc, where, "leaf pairs %s get both the SVI and the l2vlan" % sorted(overlap)))
    return findings


def run_checks(hostvars, dcs, vni_ranges, default_pool):
    """All findings for the given ``{dc: [hosts]}``."""
    findings = check_vni_ranges(vni_ranges)
    for dc, hosts in sorted(dcs.items()):
        findings.extend(check_tenants_consistent(dc, hostvars, hosts))
        findings.extend(check_fabric_facts(dc, hostvars, hosts))
        findings.extend(check_services(dc, hostvars, hosts, vni_ranges, default_pool))
    return findings
