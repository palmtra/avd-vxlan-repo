"""Render the migration configlet per leaf with AVD.

eos_designs builds the full structured config of each leaf from the fabric
mirror and the services. Only the service slice is kept:

    vlans                       the repo's VLAN ids
    vlan_interfaces             the repo's SVIs
    vxlan_interface.vxlan.vlans VLAN-to-VNI maps for the repo's VLAN ids
    router_bgp.as / .vlans      the EVPN VLAN blocks for the repo's VLAN ids
    router_bgp.vrfs[].networks  `network <subnet>` for each SVI on the leaf,
                                which is how the fabric's VRFs advertise
                                subnets (VRF default excepted)

Underlay, MLAG, VRF definitions, the VXLAN source interface and every other
key are dropped, so the configlet sits beside the existing leaf config and
never restates it. eos_cli_config_gen then renders the slice to EOS CLI.

EVPN scheme, both DCs: RD ``<router-id>:<vlan id>``, route-target both
``<l2 vni>:<vlan id>``. A service without rd_override / rt_override gets
them from this scheme.
"""

import copy
import ipaddress

from .services import dc_tenants, service_vlan_ids

HEADER = (
    "! VXLAN migration services for {hostname} ({dc}).\n"
    "! Rendered by palmtra/avd-vxlan-repo with AVD {avd_version}. Do not edit in CloudVision:\n"
    "! change group_vars/{group}_SERVICES.yml and open a PR.\n"
)
PLACEHOLDER_BANNER = "! FABRIC FACTS NOT VERIFIED (_fabric_facts_verified: false): check the RDs and BGP ASN below.\n"


class RenderError(Exception):
    pass


def avd_inputs(host_vars):
    """eos_designs inputs for one leaf.

    Each leaf pair's tag is its node group name, so `tags: [<pair>]` on a
    service places it on that pair.
    """
    inputs = {key: copy.deepcopy(value) for key, value in host_vars.items() if not str(key).startswith(("cv_", "inventory_"))}
    inputs.pop("data_center", None)
    for tenant in inputs.get("tenants") or []:
        services = list(tenant.get("l2vlans") or [])
        for vrf in tenant.get("vrfs") or []:
            services.extend(vrf.get("svis") or [])
        for service in services:
            apply_evpn_scheme(service)
    for group in ((inputs.get("l3leaf") or {}).get("node_groups") or []):
        node_filter = group.setdefault("filter", {})
        tags = list(node_filter.get("tags") or [])
        if group.get("group") not in tags:
            tags.append(group.get("group"))
        node_filter["tags"] = tags
    return inputs


def evpn_scheme(service):
    """``(rd_override, rt_override)`` the fabric scheme gives one service."""
    vlan_id = service.get("id")
    vni = service.get("vni_override")
    return vlan_id, ("%s:%s" % (vni, vlan_id) if vni is not None else None)


def apply_evpn_scheme(service):
    rd_override, rt_override = evpn_scheme(service)
    service.setdefault("rd_override", rd_override)
    if rt_override is not None:
        service.setdefault("rt_override", rt_override)


def _svi_networks(svis):
    """``[{name, networks}]`` per VRF for the SVIs on one leaf."""
    by_vrf = {}
    for svi in svis:
        vrf = svi.get("vrf")
        address = svi.get("ip_address_virtual")
        if not vrf or vrf == "default" or not address:
            continue
        prefix = str(ipaddress.ip_interface(address).network)
        networks = by_vrf.setdefault(vrf, [])
        if prefix not in networks:
            networks.append(prefix)
    return [{"name": vrf, "networks": [{"prefix": p} for p in prefixes]} for vrf, prefixes in sorted(by_vrf.items())]


def slice_services(structured_config, vlan_ids, svi_ids):
    """Keep only the keys and entries this repo owns."""
    svi_names = {"Vlan%d" % vlan_id for vlan_id in svi_ids}
    result = {}
    vlans = [vlan for vlan in structured_config.get("vlans") or [] if vlan.get("id") in vlan_ids]
    if vlans:
        result["vlans"] = vlans
    svis = [svi for svi in structured_config.get("vlan_interfaces") or [] if svi.get("name") in svi_names]
    if svis:
        result["vlan_interfaces"] = svis
    for name, vxlan in (structured_config.get("vxlan_interface") or {}).items():
        maps = [item for item in ((vxlan or {}).get("vxlan") or {}).get("vlans") or [] if item.get("id") in vlan_ids]
        if maps:
            result["vxlan_interface"] = {name: {"vxlan": {"vlans": maps}}}
    router_bgp = structured_config.get("router_bgp") or {}
    bgp_vlans = [vlan for vlan in router_bgp.get("vlans") or [] if vlan.get("id") in vlan_ids]
    bgp_vrfs = _svi_networks(svis)
    if bgp_vlans or bgp_vrfs:
        result["router_bgp"] = {"as": router_bgp.get("as")}
        if bgp_vlans:
            result["router_bgp"]["vlans"] = bgp_vlans
        if bgp_vrfs:
            result["router_bgp"]["vrfs"] = bgp_vrfs
    return result


def _violations(result):
    return ["%s: %s" % (".".join(str(p) for p in violation.path), violation.message) for violation in result.validation_result.violations]


def render_dc(dc, hostvars, hosts):
    """``{hostname: {"structured_config": dict, "config": str}}`` for one DC."""
    import pyavd

    if not hosts:
        return {}
    inputs = {}
    for host in hosts:
        validated = pyavd.validate_inputs(avd_inputs(hostvars[host]))
        problems = _violations(validated)
        if problems:
            raise RenderError("%s: AVD input validation failed:\n  %s" % (host, "\n  ".join(problems)))
        inputs[host] = validated.validated_data

    try:
        facts = pyavd.get_avd_facts(inputs)
    except Exception as exc:  # AVD raises its own error types; keep the message
        raise RenderError("%s: AVD facts failed: %s" % (dc, exc)) from exc

    vlan_ids, svi_ids = service_vlan_ids(dc_tenants(hostvars, hosts))
    verified = bool(hostvars[hosts[0]].get("_fabric_facts_verified"))
    results = {}
    for host in hosts:
        try:
            full = pyavd.get_device_structured_config(host, inputs[host], facts)._as_dict()
        except Exception as exc:
            raise RenderError("%s: AVD eos_designs failed: %s" % (host, exc)) from exc
        sliced = slice_services(full, vlan_ids, svi_ids)
        validated = pyavd.validate_structured_config(sliced)
        problems = _violations(validated)
        if problems:
            raise RenderError("%s: sliced structured config is invalid:\n  %s" % (host, "\n  ".join(problems)))
        body = pyavd.get_device_config(validated.validated_data) if sliced else ""
        header = HEADER.format(hostname=host, dc=dc, avd_version=pyavd.__version__, group=dc.upper())
        if not verified:
            header += PLACEHOLDER_BANNER
        results[host] = {"structured_config": sliced, "config": header + body}
    return results
