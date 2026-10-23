"""Read the migration-owned network services out of the per-DC variables."""


def dc_tenants(hostvars, hosts):
    """The tenants list every leaf in the DC sees. They come from one
    <DC>_SERVICES group, so a leaf that sees something else is an error the
    caller reports (see checks.check_tenants_consistent)."""
    for host in hosts:
        return list(hostvars[host].get("tenants") or [])
    return []


def iter_services(tenants):
    """Yield one dict per SVI and l2vlan with where it sits in the file."""
    for t_index, tenant in enumerate(tenants or []):
        tenant = tenant or {}
        for v_index, vrf in enumerate(tenant.get("vrfs") or []):
            vrf = vrf or {}
            for s_index, svi in enumerate(vrf.get("svis") or []):
                yield {
                    "kind": "svi",
                    "tenant": tenant.get("name"),
                    "vrf": vrf.get("name"),
                    "data": svi or {},
                    "where": "tenants[%d].vrfs[%d].svis[%d]" % (t_index, v_index, s_index),
                }
        for l_index, l2vlan in enumerate(tenant.get("l2vlans") or []):
            yield {
                "kind": "l2vlan",
                "tenant": tenant.get("name"),
                "vrf": None,
                "data": l2vlan or {},
                "where": "tenants[%d].l2vlans[%d]" % (t_index, l_index),
            }


def service_vlan_ids(tenants):
    """VLAN ids and SVI ids the repo owns in one DC."""
    vlan_ids = set()
    svi_ids = set()
    for service in iter_services(tenants):
        try:
            vlan_id = int(service["data"].get("id"))
        except (TypeError, ValueError):
            continue
        vlan_ids.add(vlan_id)
        if service["kind"] == "svi":
            svi_ids.add(vlan_id)
    return vlan_ids, svi_ids


def node_groups(host_vars):
    """``{group name: [node names]}`` from the l3leaf node_groups."""
    result = {}
    for group in ((host_vars.get("l3leaf") or {}).get("node_groups") or []):
        result[str(group.get("group"))] = [str(node.get("name")) for node in group.get("nodes") or []]
    return result
