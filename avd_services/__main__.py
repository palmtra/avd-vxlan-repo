"""python -m avd_services {check,build,deploy}

check   repo rules + AVD input validation; non-zero exit on any error
build   check, then render intended/configs/<leaf>.cfg and
        intended/structured_configs/<leaf>.yml for every leaf
deploy  send one DC's rendered configlets to CVaaS: workspace submitted,
        change control left pending approval (--dry-run: build on CVaaS
        and abandon the workspace)
"""

import argparse
import sys
from pathlib import Path

import yaml

from .checks import run_checks
from .inventory import hosts_by_dc, load_inventory, load_yaml

ROOT = Path(__file__).resolve().parent.parent


def _load(root, only_dc=None):
    hostvars, _ = load_inventory(root)
    dcs = hosts_by_dc(hostvars)
    if only_dc:
        if only_dc not in dcs:
            sys.exit("unknown data center %r (known: %s)" % (only_dc, ", ".join(sorted(dcs))))
        dcs = {only_dc: dcs[only_dc]}
    vni = load_yaml(root / "vni_ranges.yml")
    return hostvars, dcs, vni.get("vni_ranges") or {}, vni.get("vni_default_customer_type") or "external"


def _report(findings):
    for finding in findings:
        print("%-7s %-6s %s: %s" % (finding.level.upper(), finding.dc, finding.where, finding.message))
    errors = [finding for finding in findings if finding.level == "error"]
    print("%d error(s), %d warning(s)" % (len(errors), len(findings) - len(errors)))
    return not errors


def cmd_check(args):
    hostvars, dcs, ranges, pool = _load(args.root, args.dc)
    return 0 if _report(run_checks(hostvars, dcs, ranges, pool)) else 1


def cmd_build(args):
    from .render import RenderError, render_dc

    hostvars, dcs, ranges, pool = _load(args.root, args.dc)
    if not _report(run_checks(hostvars, dcs, ranges, pool)):
        return 1
    out = Path(args.root) / "intended"
    config_dir, structured_dir = out / "configs", out / "structured_configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    structured_dir.mkdir(parents=True, exist_ok=True)
    rendered = set()
    for dc, hosts in sorted(dcs.items()):
        try:
            results = render_dc(dc, hostvars, hosts)
        except RenderError as exc:
            print("ERROR   %s" % exc)
            return 1
        for host, result in results.items():
            (config_dir / ("%s.cfg" % host)).write_text(result["config"], encoding="utf-8")
            (structured_dir / ("%s.yml" % host)).write_text(
                "---\n" + yaml.safe_dump(result["structured_config"], sort_keys=False, default_flow_style=False),
                encoding="utf-8",
            )
            rendered.add(host)
            print("rendered %s (%s)" % (host, dc))
    if not args.dc:
        # A leaf removed from inventory.yml must not leave a stale configlet behind.
        for directory, suffix in ((config_dir, ".cfg"), (structured_dir, ".yml")):
            for path in directory.glob("*" + suffix):
                if path.stem not in rendered:
                    path.unlink()
                    print("removed stale %s" % path.relative_to(args.root))
    return 0


def cmd_deploy(args):
    from .deploy import DeployError, deploy

    hostvars, dcs, _, _ = _load(args.root, args.dc)
    hosts = dcs[args.dc]
    settings = dict(hostvars[hosts[0]])
    try:
        summary = deploy(
            args.dc, hosts, Path(args.root) / "intended" / "configs", settings,
            change_name=args.change_name, description=args.description, dry_run=args.dry_run, hostvars=hostvars,
        )
    except DeployError as exc:
        print("ERROR   %s" % exc)
        return 1
    print(yaml.safe_dump(summary, sort_keys=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="avd_services", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT, help="repo root (default: this checkout)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("check", cmd_check), ("build", cmd_build)):
        command = sub.add_parser(name)
        command.add_argument("--dc", help="only this data center")
        command.set_defaults(func=func)
    command = sub.add_parser("deploy")
    command.add_argument("--dc", required=True)
    command.add_argument("--change-name", required=True, help="workspace and change control name, e.g. CHG0012345 VLAN 100")
    command.add_argument("--description", default="")
    command.add_argument("--dry-run", action="store_true", help="build on CVaaS, then abandon the workspace")
    command.set_defaults(func=cmd_deploy)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
