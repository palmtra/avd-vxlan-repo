# avd-vxlan-repo

The network services added by the legacy-VLAN to VXLAN EVPN migration, built with
[Arista AVD](https://avd.arista.com) and sent to CVaaS for approval.

**Services only.** This repo owns the VLANs, anycast SVIs, VLAN-to-VNI maps and
EVPN VLAN blocks the migration adds, nothing else. Leaf underlay, BGP neighbors,
MLAG, VRFs and the VXLAN interface stay as they are on the fabric. Each leaf
gets one configlet, `VXLAN-MIGRATION_<leaf>`, beside its existing config.

```
inventory.yml                 DCs and their leaves (VTEPs)
group_vars/
  LISLE_FABRIC.yml            production facts AVD needs (node ids, ASN, router-id). Not rendered.
  LISLE_SERVICES.yml          Lisle migration services: tenants / VRFs / SVIs / l2vlans
  OMAHA_FABRIC.yml
  OMAHA_SERVICES.yml
  all.yml                     CVaaS settings
vni_ranges.yml                per-DC VNI ranges: internal / external customers
avd_services/                 check, build (pyavd), deploy (pyavd CloudVision workflow)
intended/configs/<leaf>.cfg   rendered configlets, committed: the PR diff is the CLI change
playbooks/build.yml           AAP wrapper for build
playbooks/deploy.yml          AAP wrapper for deploy (workspace + change control, never executed)
.github/workflows/ci.yml      lint, tests, check, build, render committed
```

## How a migration lands here

1. vxlan-migration discovers the VLAN and decides the gateway (fabric or external),
   the leaf pairs that carry it and the VNI (from `vni_ranges.yml`).
2. It opens a PR adding one entry to `group_vars/<DC>_SERVICES.yml`:
   an SVI when the gateway moves to the fabric, an `l2vlan` when it stays on the firewall.
3. CI checks the rules below, renders with AVD and fails unless `intended/` matches.
   Reviewers read the per-leaf CLI in the PR diff.
4. After merge, `deploy` builds a CVaaS workspace for that DC and leaves the change
   control in **pending approval**. It is approved and run in CVaaS.

## Build

```bash
pip install -r requirements.txt
python -m avd_services check          # rules + AVD schema
python -m avd_services build          # renders intended/ (all DCs, or --dc lisle)
```

How it renders: AVD `eos_designs` builds each leaf's full structured config from
the fabric facts plus the services. It keeps only these parts for this repo's VLAN ids:

- `vlans`
- `vlan_interfaces`
- `vxlan_interface.vxlan.vlans`
- `router_bgp.as/vlans`
- for each SVI on the leaf, `network <subnet>` under its VRF

AVD `eos_cli_config_gen` renders that slice.

EVPN scheme (both DCs): RD `<router-id>:<vlan id>`, `route-target both
<l2 vni>:<vlan id>`. VRFs and their L3 VNIs already exist and are not rendered.

Example: an anycast gateway on fnts-chi01-aggacc-sw05.

```
vlan 1500
   name CUST_APP
!
interface Vlan1500
   description CUST_APP
   no shutdown
   mtu 9214
   vrf v0000001a
   ip address virtual 10.150.0.1/24
!
interface Vxlan1
   vxlan vlan 1500 vni 21500
!
router bgp 4200000203
   vlan 1500
      rd 66.180.0.23:1500
      route-target both 21500:1500
      redistribute learned
   vrf v0000001a
      network 10.150.0.0/24
```

## Service rules (`check`)

| Rule | Why |
| --- | --- |
| `vni_override` required, inside the DC's range for `_migration.customer_type` (Lisle 20000 internal / 22000 external, Omaha 10000 / 11000), unique in the DC, not in another DC's range, not a VRF's L3 VNI | VNIs are allocated by the migration, per DC |
| External gateway VLAN (`gateway_mode: external`) is on the DC's `_external_gateway_pair` (border leaves), unless `_migration.external_gateway_pair` overrides it | the firewall's leaf pair must carry the VLAN |
| `tags` = leaf pairs (node groups in `<DC>_FABRIC.yml`) | places the VLAN on the pairs with endpoints or the gateway |
| SVI: `enabled: true`, `ip_address_virtual` with prefix, no secondaries, no DHCP relay, no other keys | anycast gateway only (answers D4, D9) |
| `mtu` 68 to 9214 | legacy MTU kept, capped at the EOS SVI maximum |
| `rd_override` is a number, not a full RD; RD/RT differing from the scheme is a warning | the leaf router-id stays the RD admin field |
| SVI in VRF default is refused | subnets are advertised with `network` per VRF; the fabric never redistributes connected |
| VRF keys limited to `name`, `vrf_vni`, `vrf_id`, `description`, `svis` | VRFs exist on the fabric and are never rendered |
| One entry per VLAN, except centralized anycast: SVI on the gateway pair plus an `l2vlan` with the same name and VNI on the other pairs, no pair in both | centralized vs distributed anycast |
| VLAN id 2 to 4092 | 4093/4094 are AVD's MLAG VLANs |

## Deploy

```bash
export CV_TOKEN=...   # CVaaS RW service account
python -m avd_services deploy --dc lisle --change-name "CHG0012345 VLAN 100" --dry-run
python -m avd_services deploy --dc lisle --change-name "CHG0012345 VLAN 100"
```

This uses AVD's CloudVision workflow with a Static Configuration Studio manifest:
one root container per DC (`VXLAN Migration LISLE`), one sub-container per leaf
matched by `cv_device_tag_query`, each holding that leaf's configlet.
`preserve_existing_containers` is on, so containers this repo did not create are
not touched. `--dry-run` builds the workspace (EOS validates every configlet) and
abandons it.

AVD's default `cv_deploy` config mode is deliberately not used. It assigns each
device's config through an `avd-<serial>` container. On a fabric already deployed
by AVD, that would replace the device's existing configlet.

Deploy refuses to run while a DC's `_fabric_facts_verified` is false.

## Before the first real deploy

- Both DCs' fabric facts are production. Omaha stays `_fabric_facts_verified: false`
  until fntc-aggacc-sw14's Loopback0 is confirmed (see `group_vars/OMAHA_FABRIC.yml`).
- Confirm the fabric config is in Static Configuration Studio and that the
  `hostname:` tag query matches the leaves. Otherwise set
  `cv_device_tag_query: "device:{serial_number}"` and add `serial_number` per leaf in
  `host_vars/`.
