import pytest

from avd_services.__main__ import main
from avd_services.deploy import DeployError, build_manifest, deploy
from avd_services.inventory import load_inventory

SETTINGS = {
    "cv_server": "cv.example.invalid",
    "cv_device_tag_query": "hostname:{hostname}",
    "cv_root_container": "VXLAN Migration {dc}",
    "cv_configlet_name": "VXLAN-MIGRATION_{hostname}",
    "_fabric_facts_verified": True,
}


@pytest.fixture
def config_dir(fixture_repo):
    assert main(["--root", str(fixture_repo), "build"]) == 0
    return fixture_repo / "intended" / "configs"


def test_manifest_owns_one_root_per_dc_and_preserves_the_rest(config_dir):
    manifest = build_manifest("lisle", ["leaf1b", "leaf1a"], config_dir, SETTINGS)
    assert manifest["preserve_existing_containers"] is True
    (root,) = manifest["containers"]
    assert root["name"] == "VXLAN Migration LISLE"
    assert root["tag_query"] == "device:*"
    assert [c["name"] for c in root["sub_containers"]] == ["leaf1a", "leaf1b"]
    assert root["sub_containers"][0]["tag_query"] == "hostname:leaf1a"
    assert root["sub_containers"][0]["configlets"] == [{"name": "VXLAN-MIGRATION_leaf1a"}]
    assert manifest["configlets"][0]["file"].endswith("leaf1a.cfg")


def test_manifest_loads_into_avd(config_dir):
    from pyavd._cv.workflows.models import AvdManifest, CVManifest

    cv_manifest = CVManifest.from_avd_manifest(AvdManifest.from_dict(build_manifest("lisle", ["leaf1a"], config_dir, SETTINGS)))
    assert cv_manifest.preserve_existing_containers is True
    assert len(cv_manifest.configlets) == 1


def test_tag_query_can_use_host_vars(fixture_repo, config_dir):
    hostvars, _ = load_inventory(fixture_repo)
    hostvars["leaf1a"]["serial_number"] = "JPE123"
    settings = dict(SETTINGS, cv_device_tag_query="device:{serial_number}")
    manifest = build_manifest("lisle", ["leaf1a"], config_dir, settings, hostvars)
    assert manifest["containers"][0]["sub_containers"][0]["tag_query"] == "device:JPE123"


def test_manifest_needs_rendered_configs(tmp_path):
    with pytest.raises(DeployError, match="run build first"):
        build_manifest("lisle", ["leaf1a"], tmp_path, SETTINGS)


def test_deploy_refuses_without_token(config_dir, monkeypatch):
    monkeypatch.delenv("CV_TOKEN", raising=False)
    with pytest.raises(DeployError, match="CV_TOKEN"):
        deploy("lisle", ["leaf1a"], config_dir, SETTINGS, change_name="t")


def test_deploy_refuses_placeholder_fabric(config_dir):
    with pytest.raises(DeployError, match="not verified"):
        deploy("lisle", ["leaf1a"], config_dir, dict(SETTINGS, _fabric_facts_verified=False), change_name="t", token="x")
