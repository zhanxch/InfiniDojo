# Dependency provenance

- Infinigen: https://github.com/princeton-vl/infinigen,
  tag `v1.19.0`, commit `01c39c7f7adcf7363ccbcc57c64410c69f4a4e7c`.
  BSD-3-Clause; retain its upstream license in the dependency checkout.
  This stable version retains the asset-factory architecture and supports Python 3.11 / Blender 4.2. The older v1.10.0 requires Python 3.10 / Blender 3.6 and was not used as the runtime.
- InfiniBench: https://github.com/pittisl/infinibench,
  inspected commit `d84f0d76d30cd40cc54cfd779fff202399a669b6`.
  Design reference; not a claim of a reproduced baseline.
- RoboDojo: https://github.com/RoboDojo-Benchmark/RoboDojo.
  Runtime checkout and revision are recorded by each run. Its non-commercial
  research license applies to its code/assets. They are not relicensed or copied
  into this repository.
- Isaac Sim / Isaac Lab and CuRobo: use the RoboDojo-compatible installed runtime.
  Local paths and environment details belong in ignored `.local/` files.

This repository contains new integration and generation code. Upstream assets,
third-party implementations, downloaded checkpoints and authentication material
must not be committed as project source.
