"""Send one DC's rendered configlets to CVaaS for approval there.

Uses AVD's own CloudVision workflow (pyavd deploy_to_cv) with a Static
Configuration Studio manifest:

    <cv_root_container>              e.g. "VXLAN Migration LISLE"
      └─ <hostname>  (tag query)     configlet <cv_configlet_name>

`preserve_existing_containers` is set, so every container this repo does
not own (the fabric's existing config, the other DC) is left exactly as it
is. AVD's default `cv_deploy` config mode is not used: it puts each device's
config in an `avd-<serial>` container, which on a fabric already deployed by
AVD would replace that device's configlet assignment.

The workspace is built and submitted, and the change control is left in
"pending approval". It is never approved or run from here.
"""

import asyncio
import os
from pathlib import Path


class DeployError(Exception):
    pass


def build_manifest(dc, hosts, config_dir, settings, hostvars=None):
    """Static Configuration Studio manifest for one DC.

    The device query is a CloudVision tag query. ``{hostname}`` and any host
    variable (for example ``{serial_number}``) can be used in it.
    """
    config_dir = Path(config_dir)
    configlets = []
    sub_containers = []
    for host in sorted(hosts):
        config_file = config_dir / ("%s.cfg" % host)
        if not config_file.exists():
            raise DeployError("%s has no rendered config at %s; run build first" % (host, config_file))
        configlet = settings["cv_configlet_name"].format(hostname=host, dc=dc.upper())
        configlets.append({"name": configlet, "file": str(config_file)})
        sub_containers.append({
            "name": host,
            "tag_query": settings["cv_device_tag_query"].format(**dict((hostvars or {}).get(host) or {}, hostname=host, dc=dc.upper())),
            "configlets": [{"name": configlet}],
        })
    return {
        "preserve_existing_containers": True,
        "configlets": configlets,
        "containers": [{
            "name": settings["cv_root_container"].format(dc=dc.upper()),
            "tag_query": "device:*",
            "description": "VXLAN migration services for %s. Managed by palmtra/avd-vxlan-repo." % dc,
            "sub_containers": sub_containers,
        }],
    }


def deploy(dc, hosts, config_dir, settings, change_name, description="", dry_run=False, token=None, hostvars=None):
    """Return a summary dict. ``dry_run`` builds on CVaaS and abandons the workspace."""
    from pyavd._cv.workflows.deploy_to_cv import deploy_to_cv
    from pyavd._cv.workflows.models import (
        AvdChangeControl,
        AvdManifest,
        AvdWorkspace,
        CloudVision,
        CVChangeControl,
        CVWorkspace,
    )

    token = token or os.environ.get("CV_TOKEN")
    if not token:
        raise DeployError("CV_TOKEN is not set (CVaaS RW service-account token)")
    if not settings.get("_fabric_facts_verified"):
        raise DeployError("%s fabric facts are placeholders; set _fabric_facts_verified after checking them" % dc)

    manifest = AvdManifest.from_dict(build_manifest(dc, hosts, config_dir, settings, hostvars))
    cloudvision = CloudVision(
        servers=settings["cv_server"], token=token, username=None, password=None, verify_certs=True,
        proxy_host=None, proxy_port=None, proxy_username=None, proxy_password=None,
    )
    workspace = CVWorkspace(avd_workspace=AvdWorkspace(
        name=change_name, description=description, requested_state="abandoned" if dry_run else "submitted",
    ))
    change_control = None if dry_run else CVChangeControl(avd_change_control=AvdChangeControl(
        name=change_name, description=description, requested_state="pending approval",
    ))
    result = asyncio.run(deploy_to_cv(
        cloudvision=cloudvision, workspace=workspace, change_control=change_control, static_config_manifest=manifest,
    ))
    summary = {
        "failed": bool(result.failed),
        "errors": [str(error) for error in result.errors],
        "warnings": [str(warning) for warning in result.warnings],
        "workspace": {"id": workspace.id, "state": workspace.state},
        "change_control": {"id": change_control.id, "state": change_control.state} if change_control else None,
    }
    if summary["failed"]:
        raise DeployError("CVaaS deploy failed: %s" % "; ".join(summary["errors"]))
    return summary
