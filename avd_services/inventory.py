"""Load inventory.yml and group_vars/host_vars into per-host variables.

Follows Ansible's rules for the parts this repo uses: group_vars/all.yml
first, then groups from shallowest to deepest (alphabetical within a depth),
then host_vars. Top-level keys replace, they do not merge
(hash_behaviour=replace, Ansible's default).
"""

from pathlib import Path

import yaml


def load_yaml(path):
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _group_file(directory, name):
    for suffix in (".yml", ".yaml"):
        candidate = Path(directory) / (name + suffix)
        if candidate.exists():
            return load_yaml(candidate)
    return {}


def _walk(name, body, parents, groups):
    """Collect each group's parents, direct hosts and inline vars. A group
    may be declared in several places; the declarations are merged."""
    entry = groups.setdefault(name, {"parents": set(), "hosts": {}, "vars": {}})
    entry["parents"].update(parents)
    body = body or {}
    for host, host_vars in (body.get("hosts") or {}).items():
        entry["hosts"].setdefault(host, {}).update(host_vars or {})
    entry["vars"].update(body.get("vars") or {})
    for child, child_body in (body.get("children") or {}).items():
        _walk(child, child_body, {name}, groups)


def _depth(name, groups, seen=()):
    parents = groups[name]["parents"]
    if not parents or name in seen:
        return 0
    return 1 + max(_depth(parent, groups, seen + (name,)) for parent in parents)


def _ancestors(name, groups):
    found = set()
    stack = [name]
    while stack:
        for parent in groups[stack.pop()]["parents"]:
            if parent not in found:
                found.add(parent)
                stack.append(parent)
    return found


def load_inventory(root):
    """Return ``{hostname: vars}`` and ``{hostname: [groups]}``."""
    root = Path(root)
    tree = load_yaml(root / "inventory.yml")
    groups = {}
    _walk("all", tree.get("all") or {}, set(), groups)

    host_groups = {}
    for group, entry in groups.items():
        for host in entry["hosts"]:
            host_groups.setdefault(host, set()).add(group)
    for host, direct in host_groups.items():
        expanded = set(direct)
        for group in direct:
            expanded |= _ancestors(group, groups)
        expanded.add("all")
        host_groups[host] = sorted(expanded, key=lambda g: (_depth(g, groups), g))

    hostvars = {}
    for host, member_of in sorted(host_groups.items()):
        merged = {}
        for group in member_of:
            merged.update(groups[group]["vars"])
            merged.update(_group_file(root / "group_vars", group))
        for group in member_of:
            merged.update(groups[group]["hosts"].get(host, {}))
        merged.update(_group_file(root / "host_vars", host))
        merged["inventory_hostname"] = host
        hostvars[host] = merged
    return hostvars, host_groups


def hosts_by_dc(hostvars):
    """``{data_center: [hostnames]}``. Hosts without data_center are left out."""
    result = {}
    for host, host_vars in sorted(hostvars.items()):
        dc = str(host_vars.get("data_center") or "").strip()
        if dc:
            result.setdefault(dc, []).append(host)
    return result
