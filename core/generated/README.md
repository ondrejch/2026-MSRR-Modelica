# Generated PlantData

These `.mo` packages are emitted from `data/plants/msrr/` YAML. They are
tracked source: a fresh clone cannot compile without them. Commit them
together with the YAML and `helpers/emit_modelica_plant.py`.

```bash
python3.12 -m helpers.emit_modelica_plant
```

Do not edit them. Members are `final constant` (OpenModelica rejects
non-constant package variables). Lumped 1R/9R models in `MSRR.mo` bind
`MSRR_PlantData` (load it first). SegmentedMSR binds
`SegmentedMSR_PlantData` (kelvin; load it before `SegmentedMSR.mo`).
