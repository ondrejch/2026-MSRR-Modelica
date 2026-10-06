# MSRR plant YAML

`--core_model 1r` reads `cores/r1.yaml`; `--core_model 9r` reads `cores/r9.yaml`;
`--core_model 1r10seg` reads `cores/r1_10seg.yaml` (the 1-channel x
10-axial-segment core; segmented package only); `--core_model r5x5_z10` reads
`cores/r5x5_z10.yaml` (the 5x5-radial x 10-axial-segment core; segmented
package only). Shared files apply to all four.

| File | Holds | Does not hold |
|---|---|---|
| `plant.yaml` | id, nominal power, includes | equations, solver |
| `shared/kinetics.yaml` | λ, β, Λ, n_floor, source scale, lumped τ as **named conventions** | 1R vs 9R splits |
| `shared/decay_heat.yaml` | 3-group DHYG/DHlamG, rounded `nom_frac = 0.068` | — |
| `shared/materials.yaml` | fuel, graphite, coolant, HX tube | — |
| `shared/primary_loop.yaml` | named volumes in connect order, vdot, DHRS | core cell volumes |
| `shared/secondary_loop.yaml` | HX vols/UA/geometry, UHX vol, pipes | demand **schedule** |
| `shared/pumps.yaml` | tripK, freeConvFF, default tripTime | ramp **schedule** |
| `shared/poisons.yaml` | Homogeneous Te/I/Xe and Pm/Sm data, independent yields, reduced-order worth. Off by default. | Spatial inventories, per-cell worth |
| `shared/core_vessel.yaml` | Outer-core fuel annulus / reactor-vessel / thermostated-cavity decision record (disabled by default, `enabled: false`; maturity `exploratory_geometry`), **sourced-but-blocked**: PSAR Rev. 0 (S6, ML22227A203, Rev. 0 August 2022) folio citations now recorded in `doc`/`source` text and in the decision record (D7 sourcing record, D8 fission non-source: the 2022 PSAR predates the fission increment and provides no annulus fission split, so the fission vectors stay explicit zeros, not an inherited default); inventory policy `split_existing`, topology series-location code 1 (disabled), production enablement blocked — the PSAR states the inventory split (400 L in-vessel / ~500 L operating loop), the SS316H identity, and the graphite lattice outer radius (65.0 cm, Table 4.2-1 folio 4-19), but no vessel inner/outer radii, per-segment lengths, designed series annulus, `G_modAnn`, SS316H rho/cp/k, annulus radial k, cavity surface data, or init deck fields (each remaining gap owner-decision) | Sourced geometry, vessel-material, or cavity values as deck data (the PSAR-stated values live in `doc`/`source` text and in D7, not as authored deck values; the deck's geometry nodes stay valueless placeholders); full rationale and citations live in `shared/core_vessel_decisions.md` |
| `cores/r1.yaml` | 1R volumes, UA, 7% graphite split, IF/IG, trim °C, ChannelMap placeholders | 9R arrays |
| `cores/r1_10seg.yaml` | 1R x 10-segment volumes/splits — uniform first-cut partition of the r1 2-segment totals (no measured axial profile in the repo; refinement from measured data is follow-up), dz 0.14 m, 5+5 trim mapping (TF1 → segments 1–5, TF2 → 6–10, TG → all ten) | 9R arrays |
| `cores/r5x5_z10.yaml` | 5x5-radial x 10-segment volumes/splits — uniform first-cut partition of the r1 totals over the 25-channel staggered triangular lattice (no measured radial power/flow profile in the repo; refinement from measured data is follow-up), dz 0.14 m, inventory-consistent effective lattice (pitch 0.2668 m, chanR 0.0603 m; physics review 2026-09-27 — replaces the 0.02 m placeholder), 5+5 trim mapping over all 25 channels (TF1 → segments 1–5, TF2 → 6–10, TG → all 250 cells), first non-zero product `k_mod` (86.0 W/(m.K), INL G-348 measured k(T) correlation at 570 degC; MSR-1 graphite-grade confirmation is follow-up), 5x5 neighbor-table pins | 9R arrays |
| `cores/r9.yaml` | 9-region arrays, zone flow, `nSeg = {2,6,6,4}`, **Sigma=1-renormalized** kHT graphite-direct (~6.07%), segmented-only film exponent `hAExp` (0.33; emitted only as `SegmentedMSR_PlantData.Core9R.hAExp`, review 2026-10-01 M5 — the lumped 9R keeps the `FuelChannel` default) | 1R 7% split |
| `harness/collapse_parity.yaml` | documentation-only: 1 pcm @ 2000 s, collapse `qModDirect = [0.07, 0]`. Loaded by `load_plant` for the fingerprint; **not** emitted into `PlantData`. Collapse maps bind `[Core1R.kG, 0]`. | product maps |

**Coarsening anchor.** The `r1_10seg` partition is uniform by first-cut
construction (no measured axial power or temperature profile exists in the
repository), and it is anchored by construction: grouping segments 1–5 / 6–10
reproduces the r1 2-segment values exactly (cell_vol 0.2/0.2 m³, q_fiss
0.465/0.465, q_mod 0.035/0.035, kHT 0.5/0.5). The fission split normalizes to
the uniform `fSalt = [0.1] x 10`.

**5x5 anchor.** The `r5x5_z10` partition is uniform by first-cut construction
(no measured radial power or flow profile exists in the repository), and it is
anchored to the 10-segment one-channel core by the 1/25-replica identity. The
25 channels are exact radial replicas of one another under the uniform
first-cut forcing: each carries 1/25 of the flow and heat, and the 25-column
sums reproduce the `r1_10seg` data exactly (cell_vol 0.04 m³, q_fiss 0.093,
q_mod 0.007, kHT 0.1 per segment; flow 1.0; hAnom 4565 W/K on the PSAR-basis
data, 2.4916e4 W/K before; static
partition sums are exact). In the 25-fold symmetric state every radial
neighbor delta-T is zero, so the radial conduction fluxes vanish; the radial
zero-flux argument is exact. The emitted neighbor table (56 undirected edges,
112 directed of 150 slots; per-degree channel counts {2:2, 3:5, 4:6, 5:3,
6:9}) and the 25x column sums are pinned as static initialization anchors,
and the coarsening-consistency check (`QA.CoarseningConsistencyCheck5x5Z10`,
manual QA chapter) verifies the dynamic agreement. Axial graphite conduction
remains active in the 5x5 model (the product `r1_10seg` core sets `k_mod =
0`), so dynamic agreement between the 5x5 and `r1_10seg` models is
tolerance-based, not identity-based: the trajectory coincides for every
quantity unaffected by the added axial graphite conduction, while at the
first non-zero product `k_mod` (86.0 W/(m.K)) the axial graphite links
(~30.85 W/K per link on the inventory-consistent effective lattice; the
former 0.02 m placeholder pitch gave ~0.09 W/K, ~346x too small for the node
heat capacity) leave deterministic steady-state offsets against the
10-segment reference: -2.086 K at the outlet graphite tap, +0.093 K on the
segment-5 graphite and +0.041 K on the segment-5 fuel on the PSAR-basis
data (-0.511 / +0.034 / +0.033 K before TASK-20261001-01). These are real
model differences (the 5x5 includes the axial graphite conduction the
product 10-segment core omits), not a partition defect. The coarsening QA check (`QA.CoarseningConsistencyCheck5x5Z10`) no longer compares against this product deck difference: since TASK-20261001-01 its reference core carries the matched summed axial conductance (771.37 W/K = 25 x 30.855 W/K), so the check is an exact identity (fuel and graphite taps within 1e-6 K, advected power within 1e-4 relative, at tCheck = 16000 s).

**Core maturity.** Every core deck carries two machine-readable labels,
validated at load time (`helpers/plant_config.py`) and recorded in run
manifests (`core_maturity`, `core_physical_data_maturity`; legacy 1R/9R runs
included, via `helpers.plant_config.core_maturity_labels`):

1. Implementation maturity (`maturity:`). `r1` and `r9` are
   `reference_regression` cores: the legacy computational reference that the
   regression and parity checks are built on, not a physically validated
   dataset. `r1_10seg` is a `discretization_study` and `r5x5_z10` an
   `exploratory_geometry`. Both use uniform first-cut partitions without
   independent spatial-profile validation, and `r5x5_z10` additionally
   carries a placeholder lattice pitch and a surrogate graphite conductivity
   grade pending MSR-1 grade confirmation.
2. Physical-data maturity (`physical_data_maturity:`, one of
   `reviewed_design_basis`, `legacy_inherited`, `pending_source_review`,
   `synthetic`). `r1`, `r9` and `r1_10seg` are `legacy_inherited`: their
   values come from the former Modelica implementation and have not had a
   source review (fuel and coolant properties, fuel-graphite UA, decay heat
   outside the fit interval, pump coast-down, graphite direct-heating split,
   radiating areas). `r5x5_z10` is `synthetic`.

The split followed the rev031 external review, which found that the single
`reference` label read as "physically validated".

**Shape.** Every physical quantity is a `{value, unit, doc, source}` envelope
(`value` is a scalar or homogeneous numeric array; `unit: "1"` is dimensionless);
`doc:` populates `data/CATALOG.md` and `source:` records Modelica provenance.
Bare policy scalars, such as `graphite_split_policy: raw_kHT_sum` on the 9R core,
omit the envelope. `plant.yaml` composes the deck via `includes:` across the eight
`shared/` files, the four core models (`r1`/`r1_10seg`/`r5x5_z10`/`r9`), and the
documentation-only `harness/` deck; `load_plant` merges them into one object
before validation. The table above
remains the per-file reference; the full contract is in `data/README.md`
(`data/schema/*.schema.json` is the reviewable per-level contract, and
`load_plant` enforces the same requirements at load time).

**Segmented steady state.** Each core deck ends with a segmented-only
`segmented_steady_state` block (physics review 2026-09-27). It holds the
qualified per-cell fuel-salt and graphite temperatures of the core's
segmented runner rig at 1 MW and nominal flow, plus `T_plenum` for 9R, with
temperature feedback referenced to the `reference_temperature` isothermal
zero-power state (570 °C, the lumped `core/init` convention). The emitter
writes them in kelvin as `ssTFuel`/`ssTMod` (9R: `ssTFuelChain`/`ssTModChain`)
and derives the feedback-tap setpoints `ssTF1`/`ssTF2`/`ssTG` (9R:
per-region arrays and `ssTPlenum`; the region graphite setpoint is the
`kHT_node/(kHT1 + kHT2)`-weighted mean of the region's two graphite
half-nodes, the rigs' mass-weighted tap, review 2026-10-01 M4). The segmented rigs start from these arrays
with zero initial feedback. The lumped `MSRR.mo` package does not read them.
Regenerate with `python3.12 -m helpers.segmented_trim_levels --write`,
followed by `python3.12 -m helpers.emit_modelica_plant`. The command works
at its defaults for all four cores: each core runs to 150000 s, and again
with the horizon doubled (up to 600000 s) while any temperature still moves
more than 1e-5 K over the last 10 % of the run; a block is written only from
a run that met that bar (review 2026-10-01 M9). After any data or model
change, `python3.12 -m helpers.segmented_trim_levels --check` recomputes the
blocks and exits 1 when one differs from its deck by more than that bar
(review 2026-10-01 M8).

**Kinetics `nu`.** `shared/kinetics.yaml` carries `nu` (2.43 neutrons per
fission). SegmentedMSR `PKE_T` and, since the second pass of the physics
review 2026-09-27, the legacy `SMD_MSR_Modelica.Nuclear.PKE`/`mPKE` (the
`MSRR.mo` assemblies bind `MSRR_PlantData.Kinetics.nu` and
`.energyPerFission` into `mPKE`) use it to
normalize an absolute external source S [n/s] by the full-power population
N0 = LAMBDA*nu*P/E_f = 1.966e13 on the PSAR-basis LAMBDA (1.82e13 before;
emitted as `Kinetics.fullPowerPopulation` in
both PlantData packages), and `HomogeneousPoisons` uses it for the derived
worth denominator. `source_scale` (1.58e20) is the former lumped divisor,
kept only as a documented LEGACY value (emitted as
`MSRR_PlantData.Kinetics.sourceScale`, read by no model; still inlined in
the byte-frozen `SegmentedMSR.Verify.RefMPKE`). It is not a physical
population.

**Homogeneous poisons.** `shared/poisons.yaml` is one global inventory per
isotope for the mixed primary fuel salt, not a spatial network. Yields are
independent (not cumulative). Flux averaging is `core_volume`. Microscopic
Xe/Sm cross-sections are authored in barns (`unit: b`); the reference flux
is authored in n/(cm2.s) (`unit: 1/(cm2.s)`). The segmented emitter
converts those to SI before writing `PlantData`. The worth denominator is
DERIVED, not authored (physics review 2026-09-27): the critical-core
one-group absorption `Sigma_a = kinetics.nu * F0/(phi0*V_avg)`
(`V_avg = V_core` for `core_volume`), so the worth is consistent with the
same `F0`, `phi0` and `nu` that set production and burnup (1.8958 1/m at
0.4 m3; Xe -1287 pcm, Sm -441 pcm at equilibrium). The deck key
`sigma_a_fuel` (1.0 1/m) stays only as a documented LEGACY value that the
current library no longer reads; it keeps the pre-2026-09-27 loader and
library copies used by the history-replay tests binding. The Pm-149 entry
is the cumulative A=149 chain-entry yield, because Nd-149 is not modeled.
`phi0` (1e13 n/(cm2.s)) is flagged as likely 5-10x high against the PSAR
power density and is kept as authored pending an MSRR flux calculation. The
dataset
id is `msrr_reference_v1` (reduced-order, pending scientific review). The
deck carries a required `maturity:` label, a load-time-validated plant
enum (`reference` or `reduced_order_pending_review`); the committed value
is `reduced_order_pending_review`, which is not approved for production or
publication use. The emitter writes the label beside the dataset identity
as the `SegmentedMSR_PlantData.Poisons.maturity` constant. A poison-on run
on a non-approved dataset is refused before build unless the runner is
passed the explicit development override `--allow-unreviewed-poison-data`
(`startup.runMSRR`, `freq.runFreqNominalParallel`,
`transients.run_nonlinear_steps`), and publication approval
(`freq.verify_campaign --exit_policy publication_approved`) refuses
poison-feedback results from a non-approved dataset even when numerical
quality is green. Tracking and feedback stay off on every production
vehicle unless a scenario `poisons:` block enables them on
`--package segmented`. Zero
delta poison feedback means no change from the initialized full-power
Xe+Sm worth, not zero absolute worth. Lumped `MSRR_PlantData` does not
emit this section; segmented `SegmentedMSR_PlantData.Poisons` does. The
independent Python equilibrium is `helpers/poison_oracle.py`.

The optional intra-channel radial stack is a per-core opt-in via the
`intra_channel_radial:` block on any core record (schema
`data/schema/plant.schema.json`, `$defs.intraChannelRadial`). Three
structural decisions -- stack present, annular fluid static or
circulating in a closed fixed-inventory loop, and a dedicated
finite-conductance loop heat exchanger -- admit exactly four valid
combinations, and the heat exchanger is refused unless the stack is
enabled AND the annular fluid circulates. An absent block or
`enabled: false` is the disabled default: the direct fuel-to-moderator
conductance path is unchanged, and the generated radial record is the
canonical disabled configuration (additive and byte-stable across the
shipped cores; a disabled block carries no identity into the generated
record -- `radialMaturity` and `radialDatasetId` come out empty, with
all physics at zero). When enabled the block states the physical-channel
mapping (`channel_meaning: literal_tube` -- exactly one physical fuel
tube per modeled coarse thermal channel, `channel_multiplicity` N_c =
1 -- or `channel_meaning: aggregate_bundle` -- a bundle of N_c identical
parallel fuel tubes, N_c >= 2), carries the shared concentric radii
(`0 < fuel_radius < pipe_outer_radius < annulus_outer_radius`) plus the
explicit per-segment physical radial length `geometry.physical_length`
(one positive entry per core axial segment; the stack binds these
lengths, never the moderator center spacing `dz`), the per-interface
contacts (`perfect`, an explicit mode selection, or `film` with `h >
0`), and the optional direct-deposition shares (`pipe_fraction`,
`annular_fluid_fraction`, default 0, all shares summing to 1 within
1e-9); circulating mode additionally carries the `annular_loop`
section (`nominal_mass_flow` for the whole bundle -- the per-tube flow
is `nominal_mass_flow/N_c` implicitly --, `max_flow_command` >= 1, one
nonnegative `channel_flow_fractions` entry per annular loop channel
summing to 1 within 1e-6, strictly positive `supply_plenum_volume` and
`return_plenum_volume` (whole-bundle totals), `connecting_pipe_volume`
>= 0). Multiplicity scales the radial chain through the
bundle-equivalent length N_c*L_j: per-cell pipe/annular volumes and
capacities x N_c (C_equiv = N_c*C_single), radial resistances / N_c
(R_equiv = R_single/N_c); the loop mass flow, channel flow fractions,
plenum/connecting-pipe volumes, and heat-exchanger UA/loop-side volume
are authored whole-bundle totals and are not scaled. The heat
exchanger's `model` field is its identity, not a selector: exactly
`finite_conductance_prescribed_sink` while enabled and `none` while
disabled (absent canonicalizes to `none`); any other string is refused
at load time, naming
`cores.<core>.intra_channel_radial.annular_loop.heat_exchanger.model`.
Under the default
`geometry_policy: strict_physical` the load-time validation checks the
record against the core: the stated per-segment lengths equal the
core's authored fuel lengths (`channel_geom` `LF` chain) within
`volume_tolerance` (default 1e-6; never `dz` unless proven equal --
`r1_10seg`'s 0.1575 m vs 0.14 m fails that proof), the per-cell fuel
volume N_c*pi*fuel_radius^2*L_j reproduces the declared salt inventory,
the annulus outer diameter does not exceed the lattice pitch or any
pairwise channel center distance, and the one-tube envelope
pi*annulus_outer_radius^2 does not exceed the lattice cell area (per
tube, not scaled by N_c). `strict_physical` is not complete cell
closure, though: it closes the fuel volume and the one-tube packing
against the lattice, but the validator cannot close the aggregate
moderator inventory or the N_c-scaled axial conduction sections
against the N_c lattice cells (no physical lattice dataset exists), so
it is refused outright for `channel_meaning: aggregate_bundle` (named
at `cores.<core>.intra_channel_radial.channel_meaning`) and unless the
core's `channel_map` declares `lattice_geometry: physical` (an absent
declaration canonicalizes to `placeholder`, so the shipped cores'
documented placeholder `pitch` cannot back a strict claim); a real
geometry dataset with a `lattice_geometry: physical` declaration
remains a future owner option; until then `effective_thermal` is the
honest policy for both the bundle and the placeholder lattice.
`effective_thermal` permits a reported discrepancy (with
`volume_tolerance` refused). All
physics quantities are quantity envelopes with units (`channel_meaning`
is a bare policy string); `maturity` and `dataset_id` are required when
enabled, and the deck `materials` must then define the conducting
records `channel_pipe` and `annular_fluid` (each `rho`, `cp`, `k`,
strictly positive). Lumped `MSRR_PlantData` does
not emit this section; segmented `SegmentedMSR_PlantData` does.
Production plant-chain binding (rev019 Phase 5,
`.collab/tasks/TASK-20260915-01.md`): for the 1R, 1r10seg, and 5x5
production cores the emitted `IntraChannelRadial{1R,1R_10Seg,R5x5_Z10}`
packages are what the production vehicles bind -- the six rank-matching
`SegmentedMSR.Reactors` cores (`MSRR1R`, `R1MSRRuhxTrimThermalSS`,
`MSRR1R_10Seg`, `R1MSRRuhx10SegTrimThermalSS`, `MSRR5x5_Z10`,
`R5x5Z10MSRRuhxTrimThermalSS`) bind all 42 record fields field-by-field
from the matching generated package, so deck -> generated record ->
executed parameters -> run manifest is one traceable chain for those
topologies. The production runners wire the same chain end to end
through `helpers.segmented_runs.radial_run_contract` (record + shape +
command + refusals): `startup/runMSRR.py`,
`freq/runFreqNominalParallel.py`, and
`transients/run_nonlinear_steps.py` (the trip wrappers share the
transients path) load the deck, derive the fingerprint-active
`intra_channel_radial` record (circulating records carry
`channelFlowFractions`) and the compact-column shape, record the
effective `annularFlowCommand` (default 1) in the manifest overrides,
and refuse unsupported runner/core combinations before any manifest
publishes. The 9R production zone cores are not plant-driven for
radial, permanently: the rev019 Phase 6 remediation took the fallback
path, and `helpers/plant_config.py` refuses ANY enabled
`cores.r9.intra_channel_radial` block fail-closed (named path
`cores.r9.intra_channel_radial.enabled`) -- the generated four-entry
9R record is PREPARATORY-ONLY DATA THAT BINDS NOWHERE: the executable
9R topology is four independent one-channel zone cores, so the record
cannot bind any of them, and circulating radial 9R is never plant-driven
(only the 1R-family cores accept an enabled radial block). The only
executable circulating-9R radial configuration is the hard-coded
`SegmentedMSR.Demos` records: `R9MSRRuhxRadialCirculating` runs four
independent synthetic loops, one single-channel `ClosedAnnularLoop` per
zone with separate supply/return plena, per-zone flow command, and
per-zone HX selection (no shared annular return, shared plenum, or
shared HX in the package); the zone-indexed plant path (per-zone
sub-blocks, `IntraChannelRadial9R_Z1..Z4`) is recorded in the guard
comment for a future owner card and remains an open design. All shipped
production cores ship disabled, so the production binding is live and
inert; enabling it is a deck edit plus regeneration. The committed exploratory
`SegmentedMSR.Demos` vehicles (synthetic geometry,
`exploratory_geometry` maturity) carry the enabled records with
`geometry_policy: effective_thermal` -- the synthetic dimensions cannot
satisfy the `strict_physical` closure, so no committed demo claims
`strict_physical` -- and the disclosed non-closure is a factor of about
561 between the single-tube geometric fuel volume and the declared
per-segment salt inventory on the 1r/1r10seg/5x5 rigs (7.1e-5 m3 vs
0.04 m3 per segment for 1r10seg; 3.6e-4 m3 vs 0.2 m3 for the 2-segment
1r) with the annulus outer diameter 2*annulus_outer_radius = 0.040 m
exceeding the 0.020 m lattice pitch (the cell inradius is pitch/2 =
0.010 m, so the 0.040 m diameter cannot fit; envelope area
pi*annulus_outer_radius^2 = 1.26e-3 m2 about 3.1x the 4.0e-4 m2 square
cell), and factors of about 11 (zone 1) to about 296 (zone 4) against
the declared per-zone inventories on the 9R vehicle; the decks are
driven by `python3.12 -m helpers.run_radial_demo` (`--list` shows the
committed decks).

**Outer-core fuel annulus.** The plant-level `outer_fuel_annulus:` block
on `shared/core_vessel.yaml` (schema `data/schema/plant.schema.json`,
`$defs.outerFuelAnnulus`) is a sibling dataset of the intra-channel
radial stack: one azimuthally mixed outer annulus of primary fissile fuel
per core axial segment, an axially segmented steel vessel, and an ideal
fixed-temperature cavity that reports signed heating/cooling duty. It is
disabled by default (`enabled: false`) and production enablement is
blocked — no committed enabled production deck exists (the geometry is
unsourced placeholder nodes) — so every shipped run keeps the historical
vehicles, manifests, and column sets. The block also carries an
optional `fission:` sub-block (TASK-20260918-01): a conservative SPLIT
of the whole-reactor fission source across annulus segments via the
 `source_fraction` / `heat_deposition_fraction` vectors (strict
 retained-share rule `1-fsum(f) >= 1e-9` on both, hence
 0 <= sum < 1; nonzero entries illegal while off; no silent
renormalization), the `local_same_fraction` coupling policy, and the
 `zero_credit` annulus-temperature-feedback and poison-flux-exposure
 policies — shipped `enabled: false` with zero fraction vectors. Both
 `zero_credit` policies are exploratory approximations, not quantitative
 MSRR annulus-fission predictions. When
enabled, the block is consumed by the production runner contracts:
`helpers.segmented_runs.outer_fuel_annulus_run_contract` (the radial
contract clone) derives the fingerprint-active `outer_fuel_annulus`
manifest record (dataset id, fingerprint, maturity, topology/direction
codes, cavity temperature, inventory policy, and — on any enabled
state — the fission record: enabled flag, coupling policy, both
fraction vectors, both no-credit policies) and the wrapper vehicles,
and the three production runners (`startup/runMSRR.py`,
`freq/runFreqNominalParallel.py`, `transients/run_nonlinear_steps.py`)
   forward the record into the run manifest and the compact columns. A
   fission-enabled deck with all-zero fraction vectors is structurally
   inactive (TASK-20260922-03 P2): the manifest still records the
   configured flag with both vectors, but no fission columns attach and
   the Modelica outputs fold to the no-fission equations (see the
   configured-vs-active note in `data/README.md`). The
   enabled configuration executes the plan 10.6 CoreVesselAssembly wrapper
vehicles `SegmentedMSR.Reactors.R1MSRRuhx10SegOuterAnnulusTrimThermalSS`
(`...OuterAnnulusTripThermalSS` for the UHX-trip cases) — `1r10seg`
only: 9R activation is refused by name, and an enabled dataset on any
other core, or targeting a different core than the runner selected, is
refused fail-closed before any manifest publishes. Lumped
`MSRR_PlantData` does not emit this section; segmented
`SegmentedMSR_PlantData` emits `OuterFuelAnnulus1R_10Seg`.

Temperatures are stored in **degC** (lumped native). The segmented emitter adds 273.15 K.

Lumped `MSRR.mo` binds `core/generated/MSRR_PlantData.mo`. Load order: PlantData, `SMD_MSR_Modelica.mo`, `MSRR.mo`. SegmentedMSR binds `core/generated/SegmentedMSR_PlantData.mo` (kelvin). Load order: PlantData, `SegmentedMSR.mo`. Do not edit the generated `.mo`; change YAML and regenerate.

**Traps:** lumped `nomTauCore`/`nomTauLoop` (deck `lumped_tau_core`/`lumped_tau_loop`, 43.5146/10.8787 s) are the 1R core and loop volumes over `vdot_fuel`, not `V/vdot` of the 9R mesh; since the physics review 2026-09-27 the legacy 9R core (`MSRR.Components.MSRR9R`) derives its own transit times (41.01/13.38 s) from its regional volumes instead of reading them. `vdot_fuel` and `vdot_coolant` are the PSAR mass flows over the salt densities and are maintained by hand: change them with `materials.*.rho` (TASK-20261001-01). The 9R upper plenum is a fixed volume (`cores/r9.yaml vol_upper_plenum`). Segmented `pitch`/`dz`/`chanR` do not currently set inventories; `cell_vol` and `vol_graphite` do.
