# TASK-20260917-01 P1 — Outer fuel annulus, reactor vessel, and cavity:
# data and topology decision record

- Task: TASK-20260917-01, Phase P1 (data and topology decision record)
- Authoritative specification: `.collab/optional_outer_core_fuel_annulus_and_cavity_plan.md`
  (plan §13 Phase 1, §6.1, §7.1, §4.10, §3.4)
- Decision authority: task card TASK-20260917-01, planner resolutions 3, 4, and 7
- Base tree: `f1cdc60` (TASK-20260916-01 done, tree clean)
- Machine-facing fields: `data/plants/msrr/shared/core_vessel.yaml`
  (`enabled: false`; the P7 validator enforces this field set)
- Date: 2026-09-17
- Extended: TASK-20260919-01 P2 (2026-09-19) — PSAR Rev. 0 (S6) sourcing
  record (**D7**) and annulus-fission non-source (**D8**) added; D1–D6
  citations upgraded to the primary source where noted; the deck stays
  disabled and production enablement stays blocked.

This record decides, with sources or explicit unsupported markers, the five
P1 questions: inventory policy, flow topology, 1r10seg elevations,
moderator-boundary mapping policy, and precursor-importance assumption. It is
tracked under `data/plants/msrr/` because `.collab/` is gitignored. Everything
not decided here stays explicitly unsupported; nothing is invented.

Citations used throughout:

- **S1** — `latex/MSRR_journal_article/msrr_system_dynamics.tex` (read-only
  citation of the frozen tree): design-data table `tab:msrrDesignData`
  ("MSRR system design data obtained from [msrrPrelim]"), rows
  "Primary loop volume \SI{500}{L}" and "Reactor fuel volume \SI{400}{L}",
  and the residence-time subsection
  ("Using primary inventory \SI{500}{L}, in-vessel fuel inventory
  \SI{400}{L}, and nominal primary flow, model volumes are set as
  \(V_c=\SI{0.4}{m^3}\) and \(V_l=\SI{0.1}{m^3}\).").
- **S2** — `latex/MSRR_journal_article/references.bib`, `@misc{msrrPrelim}`:
  Abilene Christian University NEXT Lab, *Molten Salt Research Reactor
  Preliminary Safety Analysis Report*, NRC ADAMS ML22227A201 (2022).
- **S3** — NRC, *Safety Evaluation for Issuance of Construction Permit for the
  Molten Salt Research Reactor* (Docket 50-610), ADAMS ML24243A042,
  September 2024. Retrieved excerpts: vessel/vessel-internals fabrication from
  316H stainless steel (§3.5, §4.3); flow description "The reactor fuel
  mixture exits the top of the vessel, passes through a heat exchanger, and
  then returns to the bottom of the vessel or the RDT" (§4.5); "Unmoderated
  fuel salt is subcritical in all configurations and locations outside of the
  reactor vessel" and "subcriticality of the fuel salt at any point outside of
  the graphite core region" (§4.5); graphite dimensions preliminary, "final
  design parameters for graphite will be provided in an OL application"
  (PSAR §4.2.3 as summarized in §4.2).
- **S4** — NRC/ACU, *Fuel Qualification Methodology Topical Report*, ADAMS
  ML25016A114, January 2025. Retrieved excerpt (§2.1): "The fuel salt flows
  through the neutron moderator, which is composed of square lattice graphite
  blocks with flow channels. From the reactor vessel, the fuel salt moves into
  the Reactor Access Vessel (RAV) and the pump bowl. It is then pumped into
  the shell side of the PHX … before being directed back to the reactor vessel
  through the cold leg."
- **S5** — In-repo (verified this phase, with numbers re-computed):
  `data/plants/msrr/plant.yaml` (`total_fuel_vol: 0.5 m3`), 
  `data/plants/msrr/cores/r1.yaml` (`cell_vol: [0.2, 0.2]`,
  `channel_geom.LF: [0.7875, 0.7875]`),
  `data/plants/msrr/cores/r1_10seg.yaml` (ten × 0.04 m³ cell volumes;
  `channel_geom.LF` = 0.1575 × 10; `channel_map.dz` = 0.14 × 10),
  `data/plants/msrr/shared/primary_loop.yaml` (five named volumes summing to
  0.1000004 m³), and `core/SegmentedMSR.mo` (bind/assert line numbers listed
  in D1).
- **S6** — ACU NEXT Lab, *Molten Salt Research Reactor Preliminary Safety
  Analysis Report*, **Revision 0, August 2022**, NRC ADAMS **ML22227A203**
  (added by TASK-20260919-01 P2). Cited file: `ACU_PSAR_REV_0.pdf`,
  SHA-256 `2b82ec2b8b57c36b2e47a47de0eab90a17e87241b59b56846d483f87c7b7549d`;
  quotations are taken from the `pdftotext -layout` extraction
  (`ACU_PSAR_REV_0.txt`), and the printed page footer ("folio", e.g.
  `4-19`) is the page identifier. The 14 KiB `acu-msrr-psar-ml22227a203.pdf`
  is an ADAMS landing-page stub, **not** the document, and is never cited
  here.

## D1 — Inventory policy: `split_existing` (determination); production enablement blocked (application)

**Recorded value (`core_vessel.yaml` → `outer_fuel_annulus.inventory_policy`):
`split_existing`.** The production-enablement *application* of this policy is
**blocked** until the annulus volume is sourced; see "Enablement gate" below.

### Closure facts (S5, verified)

| Term | Value | Binding |
|---|---|---|
| Active-core channel salt, 1R | 0.2 + 0.2 = 0.4 m³ | `cores/r1.yaml cell_vol` (MSRR.Components.MSRR1R fuelchannel vol_FN1/vol_FN2) |
| Active-core channel salt, 1r10seg | 10 × 0.04 = 0.4 m³ | `cores/r1_10seg.yaml cell_vol` (uniform partition of the r1 totals) |
| External primary loop | 2.4369e-3 + 0.04 + 4.8738e-3 + 0.045379 + 7.3107e-3 = **0.1000004 m³** | `shared/primary_loop.yaml` named volumes in connect order |
| Total (cross-check) | 0.4 + 0.1000004 = **0.5000004 m³**, inside the \|·−0.500\| < 1e-6 warning tolerance | `plant.yaml total_fuel_vol: 0.5`; `core/SegmentedMSR.mo:128-132` |

### Why `split_existing` is the supported determination

The plan (§4.10) poses exactly one question: is the annulus volume already
implicit in an existing term? The cited sources answer it by *location*:

1. S1's design-data table gives a two-level location decomposition of the
   primary inventory: "Primary loop volume **500 L**" (the whole primary
   circuit) and "Reactor fuel volume **400 L**" (the fuel inside the reactor).
2. S1's residence-time subsection names the 400 L term explicitly as the
   "**in-vessel** fuel inventory" and records the modeling split that the
   repository implements: \(V_c = 0.4\) m³ (in-vessel) and \(V_l = 0.1\) m³
   (external remainder of the 500 L).
3. The feature under this card is an outer annulus of primary fuel **inside
   the reactor vessel** (plan §1.1 physical arrangement; graphite → annulus →
   steel vessel). Any such annulus fuel is in-vessel fuel. Therefore the
   legacy term that already contained it implicitly is the **in-vessel fuel
   inventory, 0.4 m³** — bound in-repo as the `cell_vol` sums — and not the
   0.1000004 m³ external-loop term (the annulus is not in the external
   piping/DHRS/HX), and not a share of the 0.5 m³ *total* (which is itself the
   sum of the two).

**S6 primary-source confirmation (TASK-20260919-01 P2).** PSAR section
4.2.1.1 (folio **4-6**) states the location split directly: "The reactor
vessel requires 400 L to fill"; the "reactor loop, during normal operation,
will be filled with approximately 500 L"; and approximately 550 L is in the
reactor *system*, the extra roughly 50 L standing in the drain tank. This
matches the S1 two-level decomposition (400 L in-vessel / 500 L loop) and
leaves the S5 closure (0.4 + 0.1000004 = 0.5000004 m³) untouched. The
550 L *system* figure is the discrepancy to watch: it is loop + drain, so
`total_fuel_vol` must NOT become 0.55 (card discrepancy 1).

Under `split_existing`, when the annulus is enabled:

- `V_active_channel_salt = 0.4 m³ − V_outer_annulus` (the annulus is carved
  out of the in-vessel aggregate);
- `V_total_fuel` stays **0.5 m³** (`plant.yaml total_fuel_vol` unchanged);
- `PowerBlock.TotalFuelVol = V_active_core + V_outer_annulus +
  V_external_primary_loop` keeps closing to 0.5 m³, so every
  `|TotalFuelVol − 0.500| < 1e-6` cross-check survives;
- `HomogeneousPoisons.V_fuel = V_total_fuel` (= 0.5 m³) and
  `V_core = V_active_core` (= 0.4 − V_annulus), exactly as plan §4.9 requires.

The disabled path keeps `cellVol = 0.4` unchanged; disabled-path identity is
release-blocking (card A3/A11).

### Why `revise_total` is not supported

`revise_total` requires a cited source demonstrating that the 0.5 m³ total
*excluded* the annulus. No such source exists: S1's "Primary loop volume 500 L"
is the location-based total of the entire primary salt circuit; an in-vessel
flowing annulus on the main pump path cannot be outside the primary loop
volume. Nothing in S3 or S4 suggests the PSAR's 500 L or 400 L figures omit
in-vessel salt. Recording a larger total without that source would be the
double-counting the plan §4.10 forbids ("never simply add annulus volume to
the current model while retaining the old loop volumes and total-fuel claim").

### Every dependent, listed (card P1 requirement)

Under split_existing with the annulus enabled, these are the affected
contracts (all line numbers verified at `f1cdc60`):

1. `data/plants/msrr/plant.yaml` `total_fuel_vol` — cross-check value;
   consumed by the lumped bind `TotalFuelVol = MSRR_PlantData.totalFuelVol`
   (`core/MSRR.mo:509, 823`). Under split_existing the value is **retained**,
   not revised.
2. `PowerBlock.TotalFuelVol := sum(cellVol) + sum(volLoop)` binds —
   `core/SegmentedMSR.mo:2432, 2845, 4565, 4994, 5348, 5806, 6978` (1R-family
   form; `volLoop` = `SegmentedMSR.Reactors.volLoop1R` in the Trim vehicles),
   and the 9R chain form
   `sum(cellVolChain9R) + sum(volLoop9R) + volUP9R` at
   `core/SegmentedMSR.mo:3405-3406, 3886, 7240-7242`. When enabled, the
   channel `cellVol` equivalent must be re-partitioned so the sum plus
   `V_outer_annulus` still closes to 0.5 m³ (plan §8.1 names the three
   quantities; the closure residual is exposed).
3. Ten `|TotalFuelVol − 0.500| < 1e-6` cross-check asserts —
   `core/SegmentedMSR.mo:2466, 2883, 3587, 4187, 4599, 5033, 5386, 5850,
   6999, 7515` (MSRR1R; R1MSRRuhxTrimThermalSS; MSRR9RThermalAdapter;
   R9MSRRuhxTrimThermalSS; MSRR1R_10Seg; R1MSRRuhx10SegTrimThermalSS;
   MSRR5x5_Z10; R5x5Z10MSRRuhxTrimThermalSS; R1RefMPKETrimSS;
   R9RefMPKETrimSS). These are the reason the split must preserve 0.5 m³.
4. `HomogeneousPoisons` binds — `V_fuel = pb.TotalFuelVol` and
   `V_core = sum(map.cellVol)` / `sum(cellVolChain9R)` at
   `core/SegmentedMSR.mo:2436-2437, 2849-2850, 3891-3892, 4569-4570,
   4998-4999, 5352-5353, 5810-5811`; defaults `V_fuel = 0.5`,
   `V_core = 0.4` at `core/SegmentedMSR.mo:1474-1475`. Enabled state per plan
   §4.9: `V_fuel` includes the annulus (stays 0.5 m³ under split_existing);
   `V_core` remains the fission-active channel volume. Still exactly five
   global poison inventories (no new spatial states).

### Enablement gate (why production enablement is blocked)

The policy *determination* is sourced; its *application* is not. The numeric
subtraction needs `V_outer_annulus`, i.e. sourced graphite outer radius,
vessel inner radius, and per-segment physical lengths. The repository has
none (repo search found no vessel radii, cavity temperature, or outer-annulus
geometry — card "Planner-verified current tree"; planner resolution 4), and
S3 records that the PSAR graphite dimensions are preliminary with final design
values deferred to the operating-license application. No radii are invented
here; the geometry fields in `core_vessel.yaml` are recorded as valueless
nodes with `status: unsupported` (declared unit stated in each `doc`). Hence
`production_enablement.status: blocked` in the
YAML, and the shipped YAML stays `enabled: false` (card A11). Sourcing real
MSRR vessel geometry and enabling a production deck is an owner decision on
top of this card's disabled capability (card "Owner follow-up"). Synthetic
`effective_thermal` demonstrations remain possible later under the card's
demo rules.

## D2 — Flow topology: blocked (plan §6.1 codes)

Recorded (`core_vessel.yaml` → `outer_fuel_annulus.topology`):

- `series_location_code: 1` — plan §6.1 code 1 is "outer annulus disabled;
  established core inlet/outlet behavior", which is exactly the shipped
  state; it is the only series code a cited source supports today.
- `enabled_series_location_code: null` (`status: unsupported`) — **blocked**:
  no cited source establishes a designed flowing fuel annulus between the
  graphite and the reactor-vessel wall. S4 (§2.1) and S3 (§4.5) describe the
  primary path as: graphite channels → exit the top of the vessel → RAV /
  pump bowl → PHX shell side → cold leg back to the vessel bottom. Neither
  document states an in-vessel annular flow segment. Plan §6.2's
  core-outlet-to-annulus return wiring is explicitly conditional
  ("Assuming the reviewed design evidence establishes…"), and plan §1.1
  forbids inferring the order/direction from an illustration.
- `flow_direction_code: null` (`status: unsupported`) — **blocked**: direction
  codes (1 = segment 1→nSeg, 2 = nSeg→1) are meaningless while disabled and
  unsourced for the enabled state. Plan §6.1: "A production dataset should
   name one topology, not leave users to guess."

**S6 primary-source confirmation (TASK-20260919-01 P2).** The PSAR describes
the in-vessel path as bottom-entering, upward flow through the graphite
channel lattice (§1.2.3.3 folio 1-5; §4.1 folio 4-1; §4.1.1 folio 4-2;
§4.2.1.5 "the flow is oriented upwards"; §4.6.1.2 folio 4-47). It
acknowledges a *space* between the graphite and the reactor vessel wall only
as a thermal-expansion volume (§4.5.3.1 folio 4-37: salt "filling the space
between the graphite and the reactor vessel wall" as the vessel expands) —
a gap, not a designed series segment. The annular downcomer between the
graphite periphery and the inside of a core barrel is an **MSRE** feature
(§1.5 folio 1-13); MSRR is "most closely related", but its own flow
description never names an annular series segment. The blocked determination
stands, now with the primary source arguing against it.

Consequence for later phases: until a reviewed dataset names the topology,
the assembly (P4) must structurally support codes 2/3 but no production deck
may claim one; the only shippable state is code 1 (disabled).

## D3 — 1r10seg elevations (plan §13 Phase 1 item 3)

Model-grid elevations are **sourced** (S5):

- `n_seg = 10`; per-segment active length `LF = 0.1575 m` (ten × 0.1575 =
  1.575 m = 2 × the r1 `LF1 = LF2 = 0.7875 m`), source
  `cores/r1_10seg.yaml channel_geom.LF` ← `cores/r1.yaml` ←
  `MSRR.R1MSRRuhx`;
- axial pitch `dz = 0.14 m` per segment (10 × 0.14 = 1.4 m = 2 × the r1
  0.7 m), source `cores/r1_10seg.yaml channel_map.dz`;
- span definition: segment j = 1…10 covers
  [(j−1) × 0.1575 m, j × 0.1575 m] measured from the **bottom** of the
  modeled active region (segment 1 = bottom), consistent with the established
  trim mapping (TF1 → segments 1–5 lower, TF2 → segments 6–10 upper).

Absolute elevations against a physical vessel datum are **unsupported**:
no vessel drawing exists in the repository, and S3 records the PSAR graphite
dimensions as preliminary (final values deferred to the OL application). For
comparison only, the cited design value "Maximum graphite height
**151.68 cm**" (S1, from msrrPrelim) is recorded alongside the modeled 1.575 m
active length — a disclosed ~5.8 cm divergence (the modeled length is the r1
inherited lump, not a surveyed graphite height). The PSAR confirms the
number directly (S6, Table 4.2-1 folio **4-19**: "Maximum height of graphite
core 151.68 cm", tabulated at **600 °C** and marked preliminary — "Final
design parameters will be provided in the Operating License application",
§4.2.3 folio 4-12). The annulus elevations follow
the model grid via the `Lcell[nSeg]` contract (nonuniform-capable; never
`L/nSeg`).

## D4 — Moderator-boundary mapping policy (plan §13 Phase 1 item 4)

Recorded policy: **`authored_matrix`**. `G_modAnn[c,j]` is authored, or
derived from a separately reviewed graphite outer-boundary geometry; it is
never inferred from the inter-channel `ChannelMap` neighbor table, and
interior channels in a multi-channel core receive zero outer-boundary
conductance (plan §4.2; card A3 requires P3 not to infer it). The numeric
conductance values are `status: unsupported` — they require the same sourced
outer-boundary geometry as the inventory subtraction. The `r5x5_z10` authored
matrix is an owner follow-up outside this card's production enablement
(planner resolution 8).

## D5 — Precursor-importance assumption (plan §13 Phase 1 item 5)

Recorded policy: **`zero_credit_no_fission_neutronic_effect`** — a documented
no-credit assumption: all annulus importance weights `w[a,i,j] = 0`, so the
annulus delayed-neutron contribution `D[a,i] = λ_i Σ_j w[a,i,j] X[a,i,j]` is
zero until a reviewed importance distribution is added. Rationale:

1. plan §4.8 explicitly sanctions "defaulting all weights to zero" as a
   *documented no-credit assumption* (and warns it is not proof that annulus
   decays have no neutronic effect);
2. it matches the established convention in the live model: in-core cells
   default to "ALL cells counted" while external loop compartments default to
   "none" (`core/SegmentedMSR.mo:1846-1848, 3046-3050`) — the annulus is
   outside the fission-producing region, i.e. loop-path semantics;
3. it is consistent with the cited subcriticality posture: unmoderated fuel
   salt outside the graphite core region is subcritical (S3, §4.5).

**S6 primary-source confirmation (TASK-20260919-01 P2).** PSAR §4.5.2.6
(folio **4-35**): "The only location within the MSRR that fuel salt can
approach criticality is the reactor vessel. Without moderation, the fuel
salt cannot become critical." §4.5.2.7 gives delayed neutrons born outside
the core an importance of 0. The zero-credit policy therefore has
primary-source support, not only the S3 summary.

The annulus precursor *states* and transport are still modeled explicitly
(card A5); only the neutronic *credit* is zero. Numeric per-cell weights are
pinned later by the generated dataset (P5/P7), not by this record.

## D6 — Vessel material, cavity, and other unsourced quantities

- **Vessel material identity: SS316H — sourced** (S3 §§3.5/4.3: the vessel /
  leak-tight boundary is fabricated from 316H stainless steel; S1 design
  table: "Piping and others — Stainless steel 316H"; **S6 primary source**,
  §4.3.3 folio **4-21**: "constructed of stainless steel 316H or equivalent
  (as determined by carbon content)" to the ASME BPVC, allowed to
  **816 °C** (1500 °F, Section III-5), 300 000-hour design life; also
  §1.2.3.3 folio 1-5 and Table 5.2-1 folio 5-4).
- **SS316H ρ, cp, k: unsupported.** The existing `materials.yaml` `hx_tube`
  values (ρ = 8774.5, cp = 450) are not attributed to any alloy in-repo and
  must not be silently reused for the vessel. The PSAR states no numbers:
  §4.3.3 points to the ASME BPVC, and §4.2.1.4 (folio 4-8) defers
  thermophysical-property evaluations to the OL application.
- **Cavity temperature, vessel UA/area/effective emissivity, insulation
  assumptions, annulus/vessel initialization temperatures: unsupported as
  deck data** — the S6 re-open (D7) sources the RTMS air temperature only
  approximately (**~600 °C**, §6.2.4.3 folio 6-8) and gives no
  UA/area/emissivity, no insulation k/thickness (§1.2.3.7 folio 1-6;
  §4.2.5 / Fig. 4.2-2 folio 4-15; §6.2.4 folio 6-7 — qualitative only), and
  no authored init fields (core-fuel 550/590/570 °C at §4.5.4.3 folio 4-38
  and nominal average vessel 600 °C at §4.5.4.4 folio 4-39 are operating
  points, not deck values). The P6 binding consumes kelvin
  (plan §7.2); any future value must arrive with its conversion source.
  Do not use `heat_loss_tinf` = 550 °C as the cavity temperature.
- The HTGR local-heat-transfer notes are **not** a source for this MSRR
  vessel (planner resolution 4; card "Planner-verified current tree").

## D7 — PSAR Rev. 0 (S6) sourcing record: sourced-but-disabled values and remaining gaps (TASK-20260919-01 P2)

P2 records the PSAR values the shipped disabled deck may NOT carry as
authored data: no `geometry:` mapping is authored while disabled (the
validator refuses enabled-path sections, `helpers/plant_config.py`), and no
cavity-surface arrays are authored on the adiabatic cavity. The YAML carries
the citations as `doc`/`source` text of existing nodes only; every number
below lives HERE until an owner decision enables the deck. The deck remains
`enabled: false`, `production_enablement.status: blocked`.

### Source identity

- Document: ACU NEXT Lab, *Molten Salt Research Reactor Preliminary Safety
  Analysis Report*, **Revision 0, August 2022**, NRC ADAMS **ML22227A203**.
- Cited file: `ACU_PSAR_REV_0.pdf` (199 MB), SHA-256
  `2b82ec2b8b57c36b2e47a47de0eab90a17e87241b59b56846d483f87c7b7549d`.
  Quotations via the `pdftotext -layout` extraction (`ACU_PSAR_REV_0.txt`);
  folio = printed page footer. The PDF is never fed to a model.
- **Not the source:** `acu-msrr-psar-ml22227a203.pdf` (14 KiB, ADAMS
  landing-page stub). Owner to delete; never cited.

### Sourced values recorded while disabled

| Quantity | Value | S6 citation | Notes |
|---|---|---|---|
| Graphite lattice outer radius (`r_g` candidate) | **65.0 cm** | Table 4.2-1 folio **4-19**: "Outer graphite lattice radius 65.0 cm" | at **600 °C**; Table 4.2-1 is preliminary (§4.2.3 folio 4-12); no reflector (§4.2.3). This is the annulus-inner-radius candidate. |
| Vessel diameter (NOT a deck field) | **≈ 132 cm (52 in.)** | §1.2.3.5 folio **1-5** | "approximately"; inner vs outer diameter **not stated**; grid-plate total Ø 130 cm (Table 4.2-2 folio 4-19) fits inside. **Not `r_vi`.** |
| Vessel height (NOT a deck field) | **≈ 179 cm (70 in.)** | §1.2.3.5 folio **1-5** | "approximately"; not a 10-vector. |
| Vessel fill | **400 L** | §4.2.1.1 folio **4-6** | matches the S1/S5 0.4 m³ in-vessel term. |
| Operating loop inventory | **≈ 500 L** | §4.2.1.1 folio **4-6** | ≈ 550 L in the reactor *system* adds ≈ 50 L drain-tank salt (card discrepancy 1). |
| Vessel material identity | **SS316H** | §4.3.3 folio **4-21** | "316H or equivalent (as determined by carbon content)"; ASME BPVC; allowed to **816 °C** (1500 °F, Section III-5); 300 000 h design life; also §1.2.3.3 folio 1-5, Table 5.2-1 folio 5-4. |
| RTMS air temperature (NOT the deck cavity setpoint) | **~600 °C** | §6.2.4.3 folio **6-8** | "roughly"; electrical-resistance heaters (§6.2.4 folio 6-7); open to the reactor enclosure atmosphere; **not** `heat_loss_tinf` = 550 °C. |
| Core-fuel design points (NOT deck init fields) | **550 / 590 / 570 °C** | §4.5.4.3 folio **4-38** | vessel inlet / outlet / average at 1 MWth; matches S1. |
| Nominal average vessel temperature | **600 °C** | §4.5.4.4 folio **4-39** | operating baseline (full power, flowing, critical, no voids, 150 kPa); an owner may adopt it as `initialization.vessel_temperature` with this citation. |
| Channel flow direction | **upward** (bottom → top) | §1.2.3.3 folio 1-5; §4.1.1 folio 4-2; §4.2.1.5 | channel-lattice flow, not an annulus; direction codes stay meaningless while disabled. |

### Table 4.2-1 supporting rows (folio 4-19, at 600 °C, preliminary)

Lattice pitch **10.16 cm**; block pitch **10.15 cm**; fuel channel Ø
**3.016 cm**; hexagonal lattice; graphite volume **1758 L**; graphite density
**1.8 g/cc**; maximum graphite height **151.68 cm**. The 3.016 cm channel and
the 1758 L / 1.8 g/cc pair match S1. Figure 4.1-3 (folio 4-5) is image-only
in the `-layout` text — no numbers recovered; none invented.

### Discrepancy 2 (load-bearing): 132 cm radius vs 132 cm diameter

**Table 4.2-1 states "Upper core radius 132 cm" while §1.2.3.5 states the
vessel is "approximately 132 cm (52 in.) in diameter" — the same number for
two different quantities (radius vs diameter).** A 132 cm *radius* cannot
fit inside a 132 cm *diameter* vessel. Therefore:

- the sourced `r_g` is the **65.0 cm** lattice outer radius (Table 4.2-1,
  same table, different row);
- "Upper core radius 132 cm" must **NOT** be recorded as `r_g` and must
  **NOT** be recorded as `r_vi` (a future enablement picking that row would
  collide with the vessel diameter);
- `r_vi` is **not** 0.66 m: the PSAR does not say whether 132 cm is the
  inner or the outer diameter and states no wall thickness (owner
  follow-up; Figure 4.1-3 may resolve it if hand-dimensioned).

### Discrepancy 4: lattice pitch

PSAR lattice pitch **10.16 cm** (Table 4.2-1, folio 4-19) vs the in-repo
placeholder `channel_map.pitch` **0.02 m**. Recorded here; the placeholder
is **not** silently retuned (owner follow-up; the r1/r1_10seg/r5x5_z10 grids
are model data, not PSAR survey data).

### Remaining gaps (each blocks enablement; `owner-decision: required`)

1. `geometry.vessel_inner_radius` (`r_vi`) — diameter ≠ inner radius; ID vs
   OD unstated; no wall thickness. `owner-decision: required`.
2. `geometry.vessel_outer_radius` (`r_vo`) — no OD or thickness stated.
   `owner-decision: required`.
3. `geometry.physical_length[10]` — no per-segment lengths (the graphite
   height 151.68 cm and the vessel height ≈ 179 cm do not give a 10-vector).
   `owner-decision: required`.
4. `axial_grid_1r10seg.datum_elevations` — Figure 4.1-3 is image-only.
   `owner-decision: required`.
5. `topology.enabled_series_location_code` / `flow_direction_code` — the
   PSAR does not name a designed series annulus and its text argues against
   one (D2). `owner-decision: required`.
6. `moderator_coupling.conductance` (`G_modAnn`) — not a PSAR-tabulated
   reduced-order quantity. `owner-decision: required`.
7. `vessel_material.rho/cp/k` — §4.3.3 cites the ASME BPVC without numbers;
   §4.2.1.4 (folio 4-8) defers thermophysical properties to the OL
   application. `owner-decision: required`.
8. `fuel_conductivity` (annulus radial k) — same §4.2.1.4 deferral; must be
   > 0 on any enabled deck. `owner-decision: required`.
9. `cavity.vessel_UA` / `vessel_area` / `vessel_effective_emissivity` — no
   source; sourced arrays on the adiabatic cavity are phantom data.
   `owner-decision: required`.
10. `initialization.annulus_temperature` / `vessel_temperature` — no
    dedicated annulus init; 600 °C is an operating point (an owner may adopt
    it with the §4.5.4.4 citation). `owner-decision: required`.
11. `inventory.subtraction` / `inventory.active_core_cell_volume` — the
    executable carve needs gaps 1–3 first. `owner-decision: required`.

Enablement therefore stays blocked for the card's reasons 1–5 (topology,
radii/lengths, enabled-path model parameters, the validator geometry rule,
explicit-zero fission); **P3 is skipped** per the card recommendation.

## D8 — Annulus-fission non-source (TASK-20260919-01 P2)

The 2022 PSAR **predates** the landed fission increment (TASK-20260918-01)
and provides **no** annulus fission split. The supporting text:

- §4.5.2.6 (folio **4-35**): "The only location within the MSRR that fuel
  salt can approach criticality is the reactor vessel. Without moderation,
  the fuel salt cannot become critical."
- §4.5.2.7: delayed neutrons born outside the core have importance 0.
- §4.6.1.2 (folio **4-47**): "Almost all of the reactor power is deposited
  within the reactor vessel, either directly within the fuel salt or within
  the reactor vessel structures" — no split to an outer annulus.
- Figures 4.5-4 / 4.5-5 are images (the text gives only the max/avg power
  densities 17.6 / 2.45 MW/m³, folio 4-37); no per-annulus f^S/f^Q exists
  in the document.

Consequently the deck's `f^S`/`f^Q` vectors stay **explicit zeros** — a
documented **non-source**, not an inherited default (planner resolution 4;
card A5). The zeros are the shipped disabled state, never sourced data;
enabling fission on them is refused (nonzero entries require the annulus
enabled, fission enabled, and full provenance). The `status: unsupported`
marker is deliberately NOT added to the fraction vectors: the
disabled-fission rule requires every entry to be 0, and the non-source
statement lives in the YAML `doc` text of both vectors and in this section.

## Master list of unsupported quantities (plan §13 Phase 1 item 6)

All carried as valueless nodes with `status: unsupported` in
`core_vessel.yaml` (declared unit stated in each `doc`). The S6 re-open
(D7) confirms every entry below is still unsupported after the PSAR review,
each with `owner-decision: required`:

1. `inventory.subtraction` value (V_outer_annulus) — needs sourced radii ×
   lengths;
2. `topology.enabled_series_location_code` and `topology.flow_direction_code`
   — needs a cited flow-route dataset (D2);
3. `axial_grid_1r10seg.datum_elevations` — needs a vessel-datum drawing
   (D3);
4. `moderator_coupling.conductance` values — needs the reviewed
   outer-boundary geometry (D4);
5. `interfaces.*.h` film coefficients (D6);
6. `vessel_material.{rho,cp,k}` (D6);
7. `cavity.*` (temperature, vessel_UA, vessel_area,
   vessel_effective_emissivity) (D6);
8. `precursor_importance.weights` numeric array (D5 — policy is recorded;
   values follow the generated dataset);
9. `initialization.{annulus_temperature,vessel_temperature}` (D6).

## Production enablement: blocked

`outer_fuel_annulus.production_enablement.status: blocked` with the four
reasons recorded in the YAML (unsourced subtraction geometry; unsourced flow
topology; unsourced cavity/vessel-surface data; unsourced vessel material
properties). No enabled production deck is added by P1; no enabled generated
record is emitted; the four established `MODEL_BY_CORE` vehicles are
untouched. Re-enabling requires an owner decision that sources the geometry
and applies D1's split without double counting (card "Owner follow-up");
the P7 validator must refuse any `enabled: true` deck while any of the
master-list placeholders above is unresolved.

TASK-20260919-01 P2 re-verified this state against the primary source
(S6; D7/D8): the sourced-but-disabled values are recorded above, the
citations live in the YAML `doc`/`source` text, and production enablement
remains blocked (P3 skipped; the D7 gaps await owner decisions).
