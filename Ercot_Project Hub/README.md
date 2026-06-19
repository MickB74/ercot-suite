# ERCOT Project Hub

Single source of truth for **what projects are loaded into the suite and how good the data behind each one is.** Everything below is auto-generated from the shared registry and the live data lake.

> Regenerate: `python3 "Ercot_Project Hub/build_hub.py"` &nbsp;|&nbsp; Machine-readable: [`data_quality.csv`](data_quality.csv) · [`data_quality.json`](data_quality.json)

## Summary

- **645 projects** loaded (245 Wind, 400 Solar)
- **Average data-quality score: 54.0/100**
- Grade distribution: A: 24, B: 71, C: 96, D: 296, F: 158
- **624/645** have an EIA-923 crosswalk · **133/645** have cached SCED actuals · **155/645** have a computed plant value · **2** have a dedicated portal

## Data-quality dimensions

| Dimension | What it measures | Source |
| --- | --- | --- |
| **Completeness** | Share of expected metadata fields present (tech-aware: wind needs turbine specs, solar needs tracking/ratio) | `ercot_assets.json` |
| **Verification** | Is the project trustworthy: EIA-923 crosswalk match, cached SCED actuals, stated location confidence | crosswalk CSV + `plant_sced/` |
| **Calibration** | Model-readiness: typical-year generation profile + computed plant value | `plant_value/` |
| **Coverage** | Which downstream tools actually consume the project | derived |

## Rollup by technology

| Tech | Projects | Capacity (MW) | Avg score | Avg complete | Crosswalk | SCED | Valued |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Solar | 400 | 71862.1 | 57.3 | 90.7% | 391/400 | 72/400 | 92/400 |
| Wind | 245 | 50803.2 | 48.5 | 69.8% | 233/245 | 61/245 | 63/245 |

## Rollup by hub

| Hub | Projects | Capacity (MW) | Avg score | Avg complete | Crosswalk | SCED | Valued |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Houston | 19 | 2534.5 | 57 | 87.9% | 18/19 | 5/19 | 5/19 |
| North | 260 | 48516.8 | 55.8 | 85.3% | 250/260 | 51/260 | 69/260 |
| Pan | 37 | 9474.2 | 48.9 | 72.7% | 35/37 | 7/37 | 10/37 |
| South | 152 | 27729.1 | 53.3 | 84.3% | 148/152 | 29/152 | 30/152 |
| West | 177 | 34410.6 | 52.5 | 79.2% | 173/177 | 41/177 | 41/177 |

## All projects (ranked by data quality)

| Project | Tech | MW | Hub | Grade | Overall | Complete | Verify | Calib | Portal |
| --- | --- | ---: | --- | :---: | ---: | ---: | ---: | ---: | --- |
| [2W Permian Solar Project Hybrid](projects/2w-permian-solar-project-hybrid.md) | Solar | 420 | West | **A** | 100 | 100% | 100 | 100 | — |
| [Azure Sky Wind](projects/azure-sky-wind.md) | Wind | 350 | North | **A** | 100 | 100% | 100 | 100 | Azure Sky Wind |
| [BT Hickerson Solar, LLC](projects/bt-hickerson-solar-llc.md) | Solar | 310 | North | **A** | 100 | 100% | 100 | 100 | — |
| [Blue Jay Solar](projects/blue-jay-solar.md) | Solar | 141.05 | North | **A** | 100 | 100% | 100 | 100 | — |
| [Bobcat Wind](projects/bobcat-wind.md) | Wind | 150 | West | **A** | 100 | 100% | 100 | 100 | — |
| [Capricorn Ridge](projects/capricorn-ridge.md) | Wind | 663 | West | **A** | 100 | 100% | 100 | 100 | — |
| [Chillingham Solar](projects/chillingham-solar.md) | Solar | 350 | North | **A** | 100 | 100% | 100 | 100 | — |
| [Frye Solar](projects/frye-solar.md) | Solar | 200 | West | **A** | 100 | 100% | 100 | 100 | — |
| [Horse Hollow](projects/horse-hollow.md) | Wind | 735.5 | West | **A** | 100 | 100% | 100 | 100 | — |
| [IP Radian, LLC](projects/ip-radian-llc.md) | Solar | 320 | North | **A** | 100 | 100% | 100 | 100 | — |
| [Maryneal Wind](projects/maryneal-wind.md) | Wind | 182.4 | North | **A** | 100 | 100% | 100 | 100 | — |
| [Mesquite Star](projects/mesquite-star.md) | Wind | 418.9 | North | **A** | 100 | 100% | 100 | 100 | — |
| [Prospero Solar](projects/prospero-solar.md) | Solar | 300 | West | **A** | 100 | 100% | 100 | 100 | — |
| [Quantum Solar](projects/quantum-solar.md) | Solar | 374.4 | North | **A** | 100 | 100% | 100 | 100 | — |
| [Roseland Solar](projects/roseland-solar.md) | Solar | 250 | North | **A** | 100 | 100% | 100 | 100 | — |
| [Shaffer Wind](projects/shaffer-wind.md) | Wind | 200 | South | **A** | 100 | 100% | 100 | 100 | — |
| [Trojan Solar Slf](projects/trojan-solar-slf.md) | Solar | 150.59 | North | **A** | 100 | 100% | 100 | 100 | — |
| [Vera Wind](projects/vera-wind.md) | Wind | 240 | North | **A** | 100 | 100% | 100 | 100 | — |
| [Wildwind](projects/wildwind.md) | Wind | 180.08 | West | **A** | 100 | 100% | 100 | 100 | — |
| [Monte Cristo Wind](projects/monte-cristo-wind.md) | Wind | 234.5 | South | **A** | 97.2 | 91.7% | 100 | 100 | — |
| [Rio Bravo Wind](projects/rio-bravo-wind.md) | Wind | 237.6 | South | **A** | 94.4 | 83.3% | 100 | 100 | — |
| [Markham Solar](projects/markham-solar.md) | Solar | 161 | North | **A** | 93.3 | 100% | 80 | 100 | Markum Solar |
| [Star Dairy](projects/star-dairy.md) | Solar | 115.61 | North | **A** | 93.3 | 100% | 80 | 100 | — |
| [Monte Cristo 1 Wind](projects/monte-cristo-1-wind.md) | Wind | 234.5 | South | **A** | 91.7 | 75% | 100 | 100 | — |
| [Route 66 Wind](projects/route-66-wind.md) | Wind | 150 | Pan | **B** | 87.8 | 83.3% | 80 | 100 | — |
| [Ajax Wind](projects/ajax-wind.md) | Wind | 200 | West | **B** | 86.7 | 100% | 60 | 100 | — |
| [Aktina Solar](projects/aktina-solar.md) | Solar | 500 | South | **B** | 86.7 | 100% | 60 | 100 | — |
| [Ash Creek Solar](projects/ash-creek-solar.md) | Solar | 408.9 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [Azalea Springs Solar](projects/azalea-springs-solar.md) | Solar | 181 | Houston | **B** | 86.7 | 80% | 80 | 100 | — |
| [Blevins Solar](projects/blevins-solar.md) | Solar | 271.58 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [Bynum Solar Project](projects/bynum-solar-project.md) | Solar | 56 | North | **B** | 86.7 | 80% | 80 | 100 | — |
| [Cameron Wind](projects/cameron-wind.md) | Wind | 165 | South | **B** | 86.7 | 100% | 60 | 100 | — |
| [Castro Solar](projects/castro-solar.md) | Solar | 224.67 | West | **B** | 86.7 | 80% | 80 | 100 | — |
| [Cuchillas PV and BESS](projects/cuchillas-pv-and-bess.md) | Solar | 305.6 | South | **B** | 86.7 | 100% | 60 | 100 | — |
| [Delilah Solar Energy II LLC](projects/delilah-solar-energy-ii-llc.md) | Solar | 310 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [Diver Solar](projects/diver-solar.md) | Solar | 225.59 | North | **B** | 86.7 | 80% | 80 | 100 | — |
| [Dry Creek Solar I](projects/dry-creek-solar-i.md) | Solar | 201.06 | Houston | **B** | 86.7 | 80% | 80 | 100 | — |
| [Eldora Solar](projects/eldora-solar.md) | Solar | 200.94 | South | **B** | 86.7 | 80% | 80 | 100 | — |
| [Eliza Solar](projects/eliza-solar.md) | Solar | 151.7 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [Five Wells Solar Center - Hybrid](projects/five-wells-solar-center-hybrid.md) | Solar | 355.4 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [Flat Top Wind](projects/flat-top-wind.md) | Wind | 200 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [Gaia Solar](projects/gaia-solar.md) | Solar | 144.02 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [Goat Mountain](projects/goat-mountain.md) | Wind | 150 | West | **B** | 86.7 | 100% | 60 | 100 | — |
| [Green Pastures](projects/green-pastures.md) | Wind | 300 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [Greyhound Solar](projects/greyhound-solar.md) | Solar | 335.45 | West | **B** | 86.7 | 100% | 60 | 100 | — |
| [GulfStar Power, LLC](projects/gulfstar-power-llc.md) | Solar | 451.6 | South | **B** | 86.7 | 100% | 60 | 100 | — |
| [Hecate Energy Outpost Solar LLC](projects/hecate-energy-outpost-solar-llc.md) | Solar | 515 | South | **B** | 86.7 | 80% | 80 | 100 | — |
| [Hereford Wind](projects/hereford-wind.md) | Wind | 200 | Pan | **B** | 86.7 | 100% | 60 | 100 | — |
| [Hidalgo Wind](projects/hidalgo-wind.md) | Wind | 100 | South | **B** | 86.7 | 100% | 60 | 100 | — |
| [Hill Solar 1](projects/hill-solar-1.md) | Solar | 405 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [Hornet Solar (TX)](projects/hornet-solar-tx.md) | Solar | 600 | Pan | **B** | 86.7 | 80% | 80 | 100 | — |
| [Ion Solar (TX)](projects/ion-solar-tx.md) | Solar | 384 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [Liberty Solar](projects/liberty-solar.md) | Solar | 100 | Houston | **B** | 86.7 | 100% | 60 | 100 | — |
| [Long Point Solar](projects/long-point-solar.md) | Solar | 120.7 | Houston | **B** | 86.7 | 100% | 60 | 100 | — |
| [Los Mirasoles Wind](projects/los-mirasoles-wind.md) | Wind | 300 | South | **B** | 86.7 | 100% | 60 | 100 | — |
| [Lumina Solar Project](projects/lumina-solar-project.md) | Solar | 326.6 | West | **B** | 86.7 | 100% | 60 | 100 | — |
| [Maryneal Solar](projects/maryneal-solar.md) | Solar | 182.4 | West | **B** | 86.7 | 80% | 80 | 100 | — |
| [Midpoint Solar](projects/midpoint-solar.md) | Solar | 98.3 | North | **B** | 86.7 | 80% | 80 | 100 | — |
| [Millers Branch 2](projects/millers-branch-2.md) | Solar | 50 | North | **B** | 86.7 | 80% | 80 | 100 | — |
| [Mockingbird Solar Center](projects/mockingbird-solar-center.md) | Solar | 471 | North | **B** | 86.7 | 80% | 80 | 100 | — |
| [Myrtle Solar, LLC](projects/myrtle-solar-llc.md) | Solar | 313 | South | **B** | 86.7 | 100% | 60 | 100 | — |
| [Nazareth Solar](projects/nazareth-solar.md) | Solar | 203 | West | **B** | 86.7 | 80% | 80 | 100 | — |
| [Norton Solar](projects/norton-solar.md) | Solar | 128.48 | West | **B** | 86.7 | 80% | 80 | 100 | — |
| [Old 300 Solar Center, LLC](projects/old-300-solar-center-llc.md) | Solar | 430 | South | **B** | 86.7 | 100% | 60 | 100 | — |
| [Pine Forest Solar](projects/pine-forest-solar.md) | Solar | 301.51 | Houston | **B** | 86.7 | 80% | 80 | 100 | — |
| [RE Maplewood](projects/re-maplewood.md) | Solar | 550 | West | **B** | 86.7 | 100% | 60 | 100 | — |
| [Red Tailed Hawk Solar LLC](projects/red-tailed-hawk-solar-llc.md) | Solar | 350 | South | **B** | 86.7 | 100% | 60 | 100 | — |
| [Roadrunner, LLC Hybrid](projects/roadrunner-llc-hybrid.md) | Solar | 400 | West | **B** | 86.7 | 100% | 60 | 100 | — |
| [Rosebud](projects/rosebud.md) | Solar | 50 | North | **B** | 86.7 | 80% | 80 | 100 | — |
| [Roseland Solar Project, LLC](projects/roseland-solar-project-llc.md) | Solar | 500 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [San Roman Wind](projects/san-roman-wind.md) | Wind | 93 | South | **B** | 86.7 | 100% | 60 | 100 | — |
| [South Plains](projects/south-plains.md) | Wind | 500 | Pan | **B** | 86.7 | 100% | 60 | 100 | — |
| [South Ranch Wind](projects/south-ranch-wind.md) | Wind | 100 | South | **B** | 86.7 | 100% | 60 | 100 | — |
| [Stafford Solar](projects/stafford-solar.md) | Solar | 250 | West | **B** | 86.7 | 100% | 60 | 100 | — |
| [Tehuacana Creek 1 Solar and BESS](projects/tehuacana-creek-1-solar-and-bess.md) | Solar | 836.8 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [Tehuacana Creek 2 Solar and BESS](projects/tehuacana-creek-2-solar-and-bess.md) | Solar | 700 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [Tyler Bluff](projects/tyler-bluff.md) | Wind | 120 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [Uvalde Solar 2](projects/uvalde-solar-2.md) | Solar | 300 | South | **B** | 86.7 | 100% | 60 | 100 | — |
| [Waco Solar](projects/waco-solar.md) | Solar | 400 | North | **B** | 86.7 | 100% | 60 | 100 | — |
| [Clairemont Solar](projects/clairemont-solar.md) | Solar | 500 | West | **B** | 83.3 | 90% | 60 | 100 | — |
| [Cottonwood Bayou Solar](projects/cottonwood-bayou-solar.md) | Solar | 350 | South | **B** | 83.3 | 90% | 60 | 100 | — |
| [Peregrine Solar](projects/peregrine-solar.md) | Solar | 300 | South | **B** | 83.3 | 90% | 60 | 100 | — |
| [Walleye Solar (Sandow II)](projects/walleye-solar-sandow-ii.md) | Solar | 369 | North | **B** | 83.3 | 90% | 60 | 100 | — |
| [Aviator Wind](projects/aviator-wind.md) | Wind | 525 | West | **B** | 82.2 | 66.7% | 80 | 100 | — |
| [Capricorn Ridge Wind LLC](projects/capricorn-ridge-wind-llc.md) | Wind | 662.5 | West | **B** | 82.2 | 66.7% | 80 | 100 | — |
| [Century Oak Wind Project, LLC](projects/century-oak-wind-project-llc.md) | Wind | 151.5 | North | **B** | 82.2 | 66.7% | 80 | 100 | — |
| [Griffin Trail Wind](projects/griffin-trail-wind.md) | Wind | 225.6 | North | **B** | 82.2 | 66.7% | 80 | 100 | — |
| [Inertia Wind Project](projects/inertia-wind-project.md) | Wind | 301 | North | **B** | 82.2 | 66.7% | 80 | 100 | — |
| [Karankawa Wind LLC](projects/karankawa-wind-llc.md) | Wind | 307.1 | South | **B** | 82.2 | 66.7% | 80 | 100 | — |
| [Lacy Creek Wind Energy Center](projects/lacy-creek-wind-energy-center.md) | Wind | 301.3 | West | **B** | 82.2 | 66.7% | 80 | 100 | — |
| [Mesquite Creek Wind](projects/mesquite-creek-wind.md) | Wind | 211.2 | West | **B** | 82.2 | 66.7% | 80 | 100 | — |
| [Priddy Wind Project](projects/priddy-wind-project.md) | Wind | 302.4 | North | **B** | 82.2 | 66.7% | 80 | 100 | — |
| [Ranchero Wind Farm LLC](projects/ranchero-wind-farm-llc.md) | Wind | 300 | West | **B** | 82.2 | 66.7% | 80 | 100 | — |
| [Tahoka Wind](projects/tahoka-wind.md) | Wind | 300 | West | **B** | 82.2 | 66.7% | 80 | 100 | — |
| [Torrecillas Wind Energy, LLC](projects/torrecillas-wind-energy-llc.md) | Wind | 300 | South | **B** | 82.2 | 66.7% | 80 | 100 | — |
| [Amerada](projects/amerada.md) | Solar | 300 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Briggs Solar, LLC](projects/briggs-solar-llc.md) | Solar | 305 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Bright Arrow Solar, LLC](projects/bright-arrow-solar-llc.md) | Solar | 300 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Brittlebush Solar and Battery Storage](projects/brittlebush-solar-and-battery-storage.md) | Solar | 400 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Clear Fork Creek Solar and BESS SLF](projects/clear-fork-creek-solar-and-bess-slf.md) | Solar | 600 | South | **C** | 73.3 | 80% | 40 | 100 | — |
| [Danish Fields Solar, LLC](projects/danish-fields-solar-llc.md) | Solar | 600 | South | **C** | 73.3 | 80% | 40 | 100 | — |
| [Delilah Solar Energy LLC](projects/delilah-solar-energy-llc.md) | Solar | 300 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Dogwood Creek Solar and BESS](projects/dogwood-creek-solar-and-bess.md) | Solar | 435.5 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Fagus Solar Park](projects/fagus-solar-park.md) | Solar | 331.6 | Pan | **C** | 73.3 | 80% | 40 | 100 | — |
| [Funston Solar](projects/funston-solar.md) | Solar | 350 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Gigawatt Solar](projects/gigawatt-solar.md) | Solar | 1004 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Goldenrod Creek Solar and BESS SLF](projects/goldenrod-creek-solar-and-bess-slf.md) | Solar | 660 | South | **C** | 73.3 | 80% | 40 | 100 | — |
| [Greasewood II LLC](projects/greasewood-ii-llc.md) | Solar | 306 | West | **C** | 73.3 | 80% | 40 | 100 | — |
| [Hanson](projects/hanson.md) | Solar | 396 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Hecate Energy Harley Hand Solar LLC](projects/hecate-energy-harley-hand-solar-llc.md) | Solar | 514 | West | **C** | 73.3 | 80% | 40 | 100 | — |
| [Hecate Energy Longhorn Solar LLC](projects/hecate-energy-longhorn-solar-llc.md) | Solar | 650 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Hollow Branch Creek 1 Solar](projects/hollow-branch-creek-1-solar.md) | Solar | 460 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Hollow Branch Creek 2 Solar and BESS SLF](projects/hollow-branch-creek-2-solar-and-bess-slf.md) | Solar | 460 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [IP Meitner Solar](projects/ip-meitner-solar.md) | Solar | 340 | Pan | **C** | 73.3 | 80% | 40 | 100 | — |
| [IP Roman Solar](projects/ip-roman-solar.md) | Solar | 425 | Pan | **C** | 73.3 | 80% | 40 | 100 | — |
| [Juno Solar Project](projects/juno-solar-project.md) | Solar | 305.6 | West | **C** | 73.3 | 80% | 40 | 100 | — |
| [La Salle Solar](projects/la-salle-solar.md) | Solar | 500 | South | **C** | 73.3 | 80% | 40 | 100 | — |
| [Lumina II Solar Project](projects/lumina-ii-solar-project.md) | Solar | 326.6 | West | **C** | 73.3 | 80% | 40 | 100 | — |
| [Lunis Creek Solar and BESS SLF](projects/lunis-creek-solar-and-bess-slf.md) | Solar | 617.1 | South | **C** | 73.3 | 80% | 40 | 100 | — |
| [Middlebrook Creek Solar and BESS](projects/middlebrook-creek-solar-and-bess.md) | Solar | 609.1 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Pecan Prairie North Solar](projects/pecan-prairie-north-solar.md) | Solar | 350 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Quantum II Solar](projects/quantum-ii-solar.md) | Solar | 374.4 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Renegade Solar Project (Dawn)](projects/renegade-solar-project-dawn.md) | Solar | 515 | Pan | **C** | 73.3 | 80% | 40 | 100 | — |
| [Rowdy Creek Solar and BESS](projects/rowdy-creek-solar-and-bess.md) | Solar | 700 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Sequoia 1](projects/sequoia-1.md) | Solar | 401.4 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Short Creek Solar](projects/short-creek-solar.md) | Solar | 606.4 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Soleil](projects/soleil.md) | Solar | 450 | North | **C** | 73.3 | 80% | 40 | 100 | — |
| [Sunscape Renewable Energy](projects/sunscape-renewable-energy.md) | Solar | 500 | South | **C** | 73.3 | 80% | 40 | 100 | — |
| [Uva Creek Solar](projects/uva-creek-solar.md) | Solar | 301 | West | **C** | 73.3 | 80% | 40 | 100 | — |
| [Anchor Wind Iii](projects/anchor-wind-iii.md) | Wind | 16 | North | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Azure Sky Wind Project, LLC Hybrid](projects/azure-sky-wind-project-llc-hybrid.md) | Wind | 350.2 | North | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [BMP Wind (TX)](projects/bmp-wind-tx.md) | Wind | 340 | North | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Bearkat](projects/bearkat.md) | Wind | 300.1 | West | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Canyon Wind Project, LLC](projects/canyon-wind-project-llc.md) | Wind | 308.8 | West | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Cedro Hill Wind LLC](projects/cedro-hill-wind-llc.md) | Wind | 300 | South | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [El Sauz Ranch Wind, LLC](projects/el-sauz-ranch-wind-llc.md) | Wind | 301 | South | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Foard City Wind](projects/foard-city-wind.md) | Wind | 352.8 | North | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [High Lonesome Wind Power, LLC Hybrid](projects/high-lonesome-wind-power-llc-hybrid.md) | Wind | 499.5 | West | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Hubbard Wind](projects/hubbard-wind.md) | Wind | 300 | North | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Hubbard Wind II](projects/hubbard-wind-ii.md) | Wind | 304.5 | North | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [IP Meitner Wind](projects/ip-meitner-wind.md) | Wind | 460 | Pan | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [IP Roman Wind](projects/ip-roman-wind.md) | Wind | 575 | Pan | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Maverick Creek Wind](projects/maverick-creek-wind.md) | Wind | 491.6 | North | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Mesquite Star](projects/mesquite-star-62587.md) | Wind | 418.9 | West | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Prairie Hill Wind Project](projects/prairie-hill-wind-project.md) | Wind | 300 | North | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Sage Draw Wind](projects/sage-draw-wind.md) | Wind | 338.4 | West | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Santa Rita East](projects/santa-rita-east.md) | Wind | 302.4 | West | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Santa Rita Wind Energy](projects/santa-rita-wind-energy.md) | Wind | 300 | West | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [South Plains II](projects/south-plains-ii.md) | Wind | 300 | West | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Stephens Ranch Wind Energy LLC](projects/stephens-ranch-wind-energy-llc.md) | Wind | 376 | West | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [TG East](projects/tg-east.md) | Wind | 336 | North | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Wake Wind Energy Center](projects/wake-wind-energy-center.md) | Wind | 443.4 | West | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Western Trail Wind, LLC](projects/western-trail-wind-llc.md) | Wind | 366.6 | North | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [White Mesa Wind](projects/white-mesa-wind.md) | Wind | 500.6 | West | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [Young Wind](projects/young-wind.md) | Wind | 500 | North | **C** | 68.9 | 66.7% | 40 | 100 | — |
| [ANSON Solar Center, LLC](projects/anson-solar-center-llc.md) | Solar | 200 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [Aragorn Solar Project](projects/aragorn-solar-project.md) | Solar | 187.2 | West | **C** | 66.7 | 100% | 100 | 0 | — |
| [Azure Sky Solar](projects/azure-sky-solar.md) | Solar | 225 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [BPL Files Solar](projects/bpl-files-solar.md) | Solar | 157.5 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [BT Cooke Solar, LLC](projects/bt-cooke-solar-llc.md) | Solar | 59 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [Briar Creek Solar 1](projects/briar-creek-solar-1.md) | Solar | 127 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [Coniglio Solar](projects/coniglio-solar.md) | Solar | 123.6 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [Corazon Energy LLC](projects/corazon-energy-llc.md) | Solar | 200 | South | **C** | 66.7 | 100% | 100 | 0 | — |
| [Crane Solar Project](projects/crane-solar-project.md) | Solar | 150 | West | **C** | 66.7 | 100% | 100 | 0 | — |
| [Elara Solar](projects/elara-solar.md) | Solar | 132.4 | South | **C** | 66.7 | 100% | 100 | 0 | — |
| [Elm Branch Solar 1](projects/elm-branch-solar-1.md) | Solar | 134.7 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [Fence Post Solar Hybrid Project, LLC](projects/fence-post-solar-hybrid-project-llc.md) | Solar | 236 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [Galloway 1 Solar Farm](projects/galloway-1-solar-farm.md) | Solar | 250 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [Greasewood Solar](projects/greasewood-solar.md) | Solar | 255 | West | **C** | 66.7 | 100% | 100 | 0 | — |
| [Grimes County Solar](projects/grimes-county-solar.md) | Solar | 210 | Houston | **C** | 66.7 | 100% | 100 | 0 | — |
| [Grizzly Ridge Solar](projects/grizzly-ridge-solar.md) | Solar | 100 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [Hermes Solar PV](projects/hermes-solar-pv.md) | Solar | 100.4 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [Holstein 1 Solar Farm](projects/holstein-1-solar-farm.md) | Solar | 200 | West | **C** | 66.7 | 100% | 100 | 0 | — |
| [Hopkins Energy LLC](projects/hopkins-energy-llc.md) | Solar | 250 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [Horizon Solar](projects/horizon-solar.md) | Solar | 200 | South | **C** | 66.7 | 100% | 100 | 0 | — |
| [Impact Solar 1](projects/impact-solar-1.md) | Solar | 198.5 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [Kellam Solar](projects/kellam-solar.md) | Solar | 59 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [Lapetus](projects/lapetus.md) | Solar | 100 | West | **C** | 66.7 | 100% | 100 | 0 | — |
| [Misae Solar](projects/misae-solar.md) | Solar | 240 | Pan | **C** | 66.7 | 100% | 100 | 0 | — |
| [Nebula Solar](projects/nebula-solar.md) | Solar | 135 | South | **C** | 66.7 | 100% | 100 | 0 | — |
| [OCI Stillhouse Solar](projects/oci-stillhouse-solar.md) | Solar | 210 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [Oberon IA](projects/oberon-ia.md) | Solar | 150 | West | **C** | 66.7 | 100% | 100 | 0 | — |
| [Phoebe Solar](projects/phoebe-solar.md) | Solar | 250 | West | **C** | 66.7 | 100% | 100 | 0 | — |
| [Sun Valley Solar Project](projects/sun-valley-solar-project.md) | Solar | 250 | North | **C** | 66.7 | 100% | 100 | 0 | — |
| [Swift Air Solar I](projects/swift-air-solar-i.md) | Solar | 145 | West | **C** | 66.7 | 100% | 100 | 0 | — |
| [Taygete Energy Project LLC](projects/taygete-energy-project-llc.md) | Solar | 255 | West | **C** | 66.7 | 100% | 100 | 0 | — |
| [Vancourt Solar Interconnections](projects/vancourt-solar-interconnections.md) | Solar | 45 | South | **C** | 66.7 | 100% | 100 | 0 | — |
| [Zier Solar](projects/zier-solar.md) | Solar | 160 | West | **C** | 66.7 | 100% | 100 | 0 | — |
| [BPL Crown Solar LLC](projects/bpl-crown-solar-llc.md) | Solar | 100 | North | **C** | 63.3 | 90% | 100 | 0 | — |
| [Limewood Bell Renewables Solar](projects/limewood-bell-renewables-solar.md) | Solar | 204 | North | **C** | 63.3 | 90% | 100 | 0 | — |
| [Rambler](projects/rambler.md) | Solar | 200 | West | **C** | 63.3 | 90% | 100 | 0 | — |
| [AP Sunray LLC](projects/ap-sunray-llc.md) | Solar | 203.5 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Accalia Point Solar, LLC](projects/accalia-point-solar-llc.md) | Solar | 190.5 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Alamo 6](projects/alamo-6.md) | Solar | 105 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Alexis Solar, LLC](projects/alexis-solar-llc.md) | Solar | 10 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Alina Energy LLC](projects/alina-energy-llc.md) | Solar | 200 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Austin TX GigaFactory](projects/austin-tx-gigafactory.md) | Solar | 8.7 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [BKV-BPP Ponder Solar](projects/bkv-bpp-ponder-solar.md) | Solar | 2.5 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [BT Signal Ranch](projects/bt-signal-ranch.md) | Solar | 50 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Bandera Electric Coop PV](projects/bandera-electric-coop-pv.md) | Solar | 1.5 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Barilla Solar](projects/barilla-solar.md) | Solar | 30.2 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Bernard Creek Solar](projects/bernard-creek-solar.md) | Solar | 230 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Blue Jay Solar I, LLC](projects/blue-jay-solar-i-llc.md) | Solar | 210 | Houston | **D** | 53.3 | 100% | 60 | 0 | — |
| [Blue Wing Solar Energy Generation](projects/blue-wing-solar-energy-generation.md) | Solar | 13.9 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Bluebell Solar](projects/bluebell-solar.md) | Solar | 30 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Bluebell Solar II](projects/bluebell-solar-ii.md) | Solar | 115 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Bovine](projects/bovine.md) | Solar | 10 | Houston | **D** | 53.3 | 100% | 60 | 0 | — |
| [Braswell Solar](projects/braswell-solar.md) | Solar | 44.2 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Brazoria County Solar Project (Danciger)](projects/brazoria-county-solar-project-danciger.md) | Solar | 200 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Brazoria West](projects/brazoria-west.md) | Solar | 200 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Brightside](projects/brightside.md) | Solar | 50.7 | South | **D** | 53.3 | 80% | 80 | 0 | — |
| [Bronson](projects/bronson.md) | Solar | 10 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Bryan Solar, LLC](projects/bryan-solar-llc.md) | Solar | 10 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Buckthorn Westex](projects/buckthorn-westex.md) | Solar | 154 | West | **D** | 53.3 | 80% | 80 | 0 | — |
| [CPS 1 Community Solar](projects/cps-1-community-solar.md) | Solar | 1 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Cascade Solar (TX)](projects/cascade-solar-tx.md) | Solar | 10 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Castle Gap Solar Hybrid](projects/castle-gap-solar-hybrid.md) | Solar | 180 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [CatanSolar](projects/catansolar.md) | Solar | 10 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Cattlemen Solar Park](projects/cattlemen-solar-park.md) | Solar | 240 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Chiltepin Solar](projects/chiltepin-solar.md) | Solar | 100 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Chisum](projects/chisum.md) | Solar | 10 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [CoServ Community Solar Station](projects/coserv-community-solar-station.md) | Solar | 2 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Commerce Solar](projects/commerce-solar.md) | Solar | 5 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Concho Pearl Solar and Storage](projects/concho-pearl-solar-and-storage.md) | Solar | 171.9 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Concho Valley Solar, LLC](projects/concho-valley-solar-llc.md) | Solar | 172.8 | West | **D** | 53.3 | 80% | 80 | 0 | — |
| [Copperhead Solar, LLC](projects/copperhead-solar-llc.md) | Solar | 150 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Cutlass Solar 1 LLC](projects/cutlass-solar-1-llc.md) | Solar | 110.9 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Cutlass Solar II](projects/cutlass-solar-ii.md) | Solar | 202.8 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Dileo Solar](projects/dileo-solar.md) | Solar | 71.4 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [E-Volve Energy Holdings LLC](projects/e-volve-energy-holdings-llc.md) | Solar | 1.2 | Houston | **D** | 53.3 | 100% | 60 | 0 | — |
| [ENGIE Long Draw Solar LLC](projects/engie-long-draw-solar-llc.md) | Solar | 225 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [East Blackland Solar Project 1](projects/east-blackland-solar-project-1.md) | Solar | 144 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [East Pecos Solar](projects/east-pecos-solar.md) | Solar | 118.5 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Echols Creek Solar](projects/echols-creek-solar.md) | Solar | 201.2 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Eddy II](projects/eddy-ii.md) | Solar | 10 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Eiffel Solar Project](projects/eiffel-solar-project.md) | Solar | 240 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Elio Energy LLC](projects/elio-energy-llc.md) | Solar | 160 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Ellis Solar LLC](projects/ellis-solar-llc.md) | Solar | 80 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Elm Flats Solar](projects/elm-flats-solar.md) | Solar | 125 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Emerald Grove](projects/emerald-grove.md) | Solar | 108 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Erin](projects/erin.md) | Solar | 202 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [FELPS 1 - Calaveras](projects/felps-1-calaveras.md) | Solar | 1 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [FELPS 3 - Floresville South](projects/felps-3-floresville-south.md) | Solar | 1 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [FELPS 4 - Floresville South](projects/felps-4-floresville-south.md) | Solar | 1 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [FELPS 5- Floresville South](projects/felps-5-floresville-south.md) | Solar | 1 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [FELPS 6- Floresville West](projects/felps-6-floresville-west.md) | Solar | 1 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [FELPS 7- Floresville West](projects/felps-7-floresville-west.md) | Solar | 1 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Fighting Jays Solar Project](projects/fighting-jays-solar-project.md) | Solar | 227.5 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Fort Bend Solar LLC](projects/fort-bend-solar-llc.md) | Solar | 240 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [G.S.E. One LLC](projects/g-s-e-one-llc.md) | Solar | 83 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Galloway 2 Solar Farm](projects/galloway-2-solar-farm.md) | Solar | 110 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Ganado Solar](projects/ganado-solar.md) | Solar | 150.5 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Griffin Solar](projects/griffin-solar.md) | Solar | 5 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Hallmark](projects/hallmark.md) | Solar | 42 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Hayhurst Texas Solar](projects/hayhurst-texas-solar.md) | Solar | 24.8 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Highpeak Solar 1](projects/highpeak-solar-1.md) | Solar | 10 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Highway 56 Solar](projects/highway-56-solar.md) | Solar | 5.3 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [IKEA Live Oak Rooftop PV System](projects/ikea-live-oak-rooftop-pv-system.md) | Solar | 1.7 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [IKEA Round Rock 027](projects/ikea-round-rock-027.md) | Solar | 1.4 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Itsee Solar](projects/itsee-solar.md) | Solar | 53.3 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Jones City 1 Solar](projects/jones-city-1-solar.md) | Solar | 215 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Jones City 2 Solar](projects/jones-city-2-solar.md) | Solar | 185 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Lamesa II](projects/lamesa-ii.md) | Solar | 50 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Lamesa Solar](projects/lamesa-solar.md) | Solar | 202 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Lampwick](projects/lampwick.md) | Solar | 7.5 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Leon Solar](projects/leon-solar.md) | Solar | 10 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Lily Solar Hybrid](projects/lily-solar-hybrid.md) | Solar | 146.7 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Long Point Solar](projects/long-point-solar-68998.md) | Solar | 120 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Marcos PV and BESS](projects/marcos-pv-and-bess.md) | Solar | 115.9 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Marlin Solar](projects/marlin-solar.md) | Solar | 5.3 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Mars Solar](projects/mars-solar.md) | Solar | 10 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Midway Solar - TX](projects/midway-solar-tx.md) | Solar | 182 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Niagara Bottling - Seguin](projects/niagara-bottling-seguin.md) | Solar | 4.9 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Noble Solar](projects/noble-solar.md) | Solar | 275 | North | **D** | 53.3 | 80% | 80 | 0 | — |
| [North Gainesville Solar](projects/north-gainesville-solar.md) | Solar | 5.2 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [OCI Alamo 2, LLC](projects/oci-alamo-2-llc.md) | Solar | 4.4 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [OCI Alamo 3 LLC](projects/oci-alamo-3-llc.md) | Solar | 5.5 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [OCI Alamo 4, LLC](projects/oci-alamo-4-llc.md) | Solar | 39.6 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [OCI Alamo 5 LLC](projects/oci-alamo-5-llc.md) | Solar | 100 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [OCI Alamo 7 LLC](projects/oci-alamo-7-llc.md) | Solar | 100 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [OCI Alamo Solar I Hybrid](projects/oci-alamo-solar-i-hybrid.md) | Solar | 40.7 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [OCI SunRoper](projects/oci-sunroper.md) | Solar | 260 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Oberon IB](projects/oberon-ib.md) | Solar | 30 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Orion I Solar Project](projects/orion-i-solar-project.md) | Solar | 200 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Orion II Solar Project](projects/orion-ii-solar-project.md) | Solar | 250 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Orion III Solar Project](projects/orion-iii-solar-project.md) | Solar | 250 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Oxy Renewable Energy - Goldsmith](projects/oxy-renewable-energy-goldsmith.md) | Solar | 16.8 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Peacock Energy Project](projects/peacock-energy-project.md) | Solar | 150 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Pearl Solar](projects/pearl-solar.md) | Solar | 50 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Phantom Solar](projects/phantom-solar.md) | Solar | 15.4 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Pitts Dudik Solar](projects/pitts-dudik-solar.md) | Solar | 49.6 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Porter Solar, LLC (TX)](projects/porter-solar-llc-tx.md) | Solar | 245 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [PowerFin Kingsbery](projects/powerfin-kingsbery.md) | Solar | 2.6 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Prospero Solar II](projects/prospero-solar-ii.md) | Solar | 250 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [RE Roserock](projects/re-roserock.md) | Solar | 160 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Roscommon Solar Park](projects/roscommon-solar-park.md) | Solar | 82.8 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [SGT Hoskins Solar Project Hybrid](projects/sgt-hoskins-solar-project-hybrid.md) | Solar | 204.1 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Samson Solar Energy II LLC](projects/samson-solar-energy-ii-llc.md) | Solar | 200 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Samson Solar Energy III LLC](projects/samson-solar-energy-iii-llc.md) | Solar | 250 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Second Division Solar](projects/second-division-solar.md) | Solar | 100 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [SolaireHolman Solar Project](projects/solaireholman-solar-project.md) | Solar | 50 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Sparta Solar](projects/sparta-solar.md) | Solar | 250 | South | **D** | 53.3 | 80% | 80 | 0 | — |
| [Stampede Solar Hybrid](projects/stampede-solar-hybrid.md) | Solar | 255.9 | North | **D** | 53.3 | 80% | 80 | 0 | — |
| [Starr Solar Ranch](projects/starr-solar-ranch.md) | Solar | 136 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Sterling Solar (TX)](projects/sterling-solar-tx.md) | Solar | 10 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [SunE CPS1 LLC](projects/sune-cps1-llc.md) | Solar | 10 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [SunE CPS2 LLC](projects/sune-cps2-llc.md) | Solar | 10 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [SunE CPS3 LLC](projects/sune-cps3-llc.md) | Solar | 10.6 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Sunrose Renewable Energy](projects/sunrose-renewable-energy.md) | Solar | 300 | Houston | **D** | 53.3 | 100% | 60 | 0 | — |
| [TPE Erath Solar, LLC](projects/tpe-erath-solar-llc.md) | Solar | 9.9 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Taygete II Energy Project](projects/taygete-ii-energy-project.md) | Solar | 203.8 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Tiger Solar](projects/tiger-solar.md) | Solar | 250 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Titan Solar Project](projects/titan-solar-project.md) | Solar | 260 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Toyota HQ Plan](projects/toyota-hq-plan.md) | Solar | 7.7 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Tres Bahias](projects/tres-bahias.md) | Solar | 196.3 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Tyson Nick](projects/tyson-nick.md) | Solar | 90.5 | North | **D** | 53.3 | 80% | 80 | 0 | — |
| [Upton County Solar](projects/upton-county-solar.md) | Solar | 150 | West | **D** | 53.3 | 100% | 60 | 0 | — |
| [Uvalde Solar 1](projects/uvalde-solar-1.md) | Solar | 150 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Verizon Hidden Ridge Solar Project](projects/verizon-hidden-ridge-solar-project.md) | Solar | 2.9 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Waco Solar II](projects/waco-solar-ii.md) | Solar | 200 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Wagyu](projects/wagyu.md) | Solar | 120 | South | **D** | 53.3 | 100% | 60 | 0 | — |
| [Walnut Springs Solar](projects/walnut-springs-solar.md) | Solar | 5 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Webberville Solar Project](projects/webberville-solar-project.md) | Solar | 30 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [West Moore Solar](projects/west-moore-solar.md) | Solar | 5 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [West Moore Solar II](projects/west-moore-solar-ii.md) | Solar | 5 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Whitesboro Solar](projects/whitesboro-solar.md) | Solar | 5 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Whitesboro Solar II](projects/whitesboro-solar-ii.md) | Solar | 5 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Whitewright Solar](projects/whitewright-solar.md) | Solar | 10 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Yaupon Solar Project (Hybrid)](projects/yaupon-solar-project-hybrid.md) | Solar | 200 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [Yellow Jacket](projects/yellow-jacket.md) | Solar | 5 | North | **D** | 53.3 | 100% | 60 | 0 | — |
| [7V Solar Ranch](projects/7v-solar-ranch.md) | Solar | 240 | North | **D** | 50 | 90% | 60 | 0 | — |
| [Angelo Solar](projects/angelo-solar.md) | Solar | 195 | West | **D** | 50 | 90% | 60 | 0 | — |
| [BPL Sol Solar LLC](projects/bpl-sol-solar-llc.md) | Solar | 100 | North | **D** | 50 | 90% | 60 | 0 | — |
| [BT Jungmann](projects/bt-jungmann.md) | Solar | 40 | North | **D** | 50 | 90% | 60 | 0 | — |
| [Big Elm Solar](projects/big-elm-solar.md) | Solar | 200 | North | **D** | 50 | 90% | 60 | 0 | — |
| [Big Star Solar, LLC (Hybrid)](projects/big-star-solar-llc-hybrid.md) | Solar | 200 | North | **D** | 50 | 90% | 60 | 0 | — |
| [FELPS 2 - Calaveras](projects/felps-2-calaveras.md) | Solar | 1 | South | **D** | 50 | 90% | 60 | 0 | — |
| [Gransolar Texas One, LLC](projects/gransolar-texas-one-llc.md) | Solar | 50 | North | **D** | 50 | 90% | 60 | 0 | — |
| [Longbow Solar, LLC](projects/longbow-solar-llc.md) | Solar | 78.1 | South | **D** | 50 | 90% | 60 | 0 | — |
| [Stoneridge Solar, LLC](projects/stoneridge-solar-llc.md) | Solar | 100 | North | **D** | 50 | 90% | 60 | 0 | — |
| [TX Fort Worth 5200 Gold Spike Drive](projects/tx-fort-worth-5200-gold-spike-drive.md) | Solar | 4 | North | **D** | 50 | 90% | 60 | 0 | — |
| [TX Houston 7080 Express Lane](projects/tx-houston-7080-express-lane.md) | Solar | 1.9 | Houston | **D** | 50 | 90% | 60 | 0 | — |
| [Texas Solar Nova 1](projects/texas-solar-nova-1.md) | Solar | 252 | West | **D** | 50 | 90% | 60 | 0 | — |
| [Texas Solar Nova 2](projects/texas-solar-nova-2.md) | Solar | 200 | West | **D** | 50 | 90% | 60 | 0 | — |
| [Amadeus Wind Farm](projects/amadeus-wind-farm.md) | Wind | 250 | West | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Appaloosa Run Wind](projects/appaloosa-run-wind.md) | Wind | 171.8 | West | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Blackjack Creek Wind Farm](projects/blackjack-creek-wind-farm.md) | Wind | 239.6 | South | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Blue Hills Wind Project](projects/blue-hills-wind-project.md) | Wind | 276 | West | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Cameron Wind 1 LLC](projects/cameron-wind-1-llc.md) | Wind | 165 | South | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Castle Gap Wind](projects/castle-gap-wind.md) | Wind | 196.8 | North | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Coyote Wind LLC](projects/coyote-wind-llc.md) | Wind | 242.5 | West | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [El Algodon Alto Wind Farm, LLC](projects/el-algodon-alto-wind-farm-llc.md) | Wind | 200.2 | South | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Fluvanna](projects/fluvanna.md) | Wind | 155.4 | West | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Goodnight](projects/goodnight.md) | Wind | 265.5 | Pan | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Gopher Creek Wind Farm](projects/gopher-creek-wind-farm.md) | Wind | 158 | West | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Hart Wind](projects/hart-wind.md) | Wind | 166.4 | Pan | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Jumbo Hill Wind Project](projects/jumbo-hill-wind-project.md) | Wind | 160.7 | West | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [La Chalupa, LLC](projects/la-chalupa-llc.md) | Wind | 198.5 | South | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Lockett Windfarm](projects/lockett-windfarm.md) | Wind | 183.8 | North | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Mesteno](projects/mesteno.md) | Wind | 201.6 | South | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Midway Wind, LLC](projects/midway-wind-llc.md) | Wind | 162.9 | South | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Palmas Wind, LLC](projects/palmas-wind-llc.md) | Wind | 144.9 | South | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Pioneer Hutt Wind Energy](projects/pioneer-hutt-wind-energy.md) | Wind | 140 | West | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Reloj del Sol Wind Farm](projects/reloj-del-sol-wind-farm.md) | Wind | 209.4 | South | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Roadrunner Wind Farm](projects/roadrunner-wind-farm.md) | Wind | 256 | North | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Shamrock Wind](projects/shamrock-wind.md) | Wind | 223.9 | West | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Stella Wind Farm](projects/stella-wind-farm.md) | Wind | 201 | South | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [West Raymond Wind Farm LLC](projects/west-raymond-wind-farm-llc.md) | Wind | 239.8 | South | **D** | 48.9 | 66.7% | 80 | 0 | — |
| [Abeja Solar Farm](projects/abeja-solar-farm.md) | Solar | 200 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Albatross Solar, LLC](projects/albatross-solar-llc.md) | Solar | 101 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Alira](projects/alira.md) | Solar | 222.8 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Armadillo Solar Center](projects/armadillo-solar-center.md) | Solar | 175 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Azalea Springs Solar Park](projects/azalea-springs-solar-park.md) | Solar | 180 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Brazoria Solar I](projects/brazoria-solar-i.md) | Solar | 125 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Brushy Creek Solar LLC](projects/brushy-creek-solar-llc.md) | Solar | 177 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Cairos Solar and Storage](projects/cairos-solar-and-storage.md) | Solar | 153.3 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Callan Solar](projects/callan-solar.md) | Solar | 90 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Cannibal Draw Solar and Storage](projects/cannibal-draw-solar-and-storage.md) | Solar | 149.5 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Cardinal Solar (TX)](projects/cardinal-solar-tx.md) | Solar | 74 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Cattlemen Solar II](projects/cattlemen-solar-ii.md) | Solar | 200 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Chisme Solar and Storage](projects/chisme-solar-and-storage.md) | Solar | 147 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Claxton Solar](projects/claxton-solar.md) | Solar | 150.6 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Compadre Solar](projects/compadre-solar.md) | Solar | 150 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Corpus Refinery](projects/corpus-refinery.md) | Solar | 27.5 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Cosper Solar](projects/cosper-solar.md) | Solar | 148.2 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Cotton Belle Solar](projects/cotton-belle-solar.md) | Solar | 80 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Dahlia Energy](projects/dahlia-energy.md) | Solar | 200 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Darkwood Solar](projects/darkwood-solar.md) | Solar | 150.7 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Decker Creek](projects/decker-creek.md) | Solar | 0.3 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Desert Vine Solar](projects/desert-vine-solar.md) | Solar | 121.3 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Despain Solar](projects/despain-solar.md) | Solar | 236.2 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Diver Solar, LLC](projects/diver-solar-llc.md) | Solar | 224 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Donegal Solar Project](projects/donegal-solar-project.md) | Solar | 204.2 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Dunlay Solar](projects/dunlay-solar.md) | Solar | 180.4 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Eagle Springs Hybrid](projects/eagle-springs-hybrid.md) | Solar | 110.1 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Edens Solar](projects/edens-solar.md) | Solar | 70 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Eldora Energy LLC](projects/eldora-energy-llc.md) | Solar | 200 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Erath County Solar](projects/erath-county-solar.md) | Solar | 204 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Estonian Solar Project, LLC](projects/estonian-solar-project-llc.md) | Solar | 204.5 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Eytcheson Solar](projects/eytcheson-solar.md) | Solar | 76.4 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Findley PV and BESS](projects/findley-pv-and-bess.md) | Solar | 50.3 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Flag City Solar](projects/flag-city-solar.md) | Solar | 167.3 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Forest Grove - Dodd](projects/forest-grove-dodd.md) | Solar | 200 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Gaia Hybrid](projects/gaia-hybrid.md) | Solar | 152.7 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Garcitas Creek Solar](projects/garcitas-creek-solar.md) | Solar | 201.9 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Greenalia Solar Power Blackwelder Ranch](projects/greenalia-solar-power-blackwelder-ranch.md) | Solar | 199.7 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Greenalia Solar Power Misae III](projects/greenalia-solar-power-misae-iii.md) | Solar | 185.4 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Harryoung PV and BESS](projects/harryoung-pv-and-bess.md) | Solar | 190 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Hill Solar II](projects/hill-solar-ii.md) | Solar | 200 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Holly Branch Solar, LLC](projects/holly-branch-solar-llc.md) | Solar | 230 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Hooper Solar and Storage](projects/hooper-solar-and-storage.md) | Solar | 50.5 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Hornet Solar II (TX) (Hybrid)](projects/hornet-solar-ii-tx-hybrid.md) | Solar | 200 | Pan | **D** | 40 | 80% | 40 | 0 | — |
| [Hutcherson Solar](projects/hutcherson-solar.md) | Solar | 123.3 | North | **D** | 40 | 80% | 40 | 0 | — |
| [IKEA Grand Prairie Rooftop PV System](projects/ikea-grand-prairie-rooftop-pv-system.md) | Solar | 1.2 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Indigo Solar & Storage](projects/indigo-solar-storage.md) | Solar | 150 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Isaac Solar](projects/isaac-solar.md) | Solar | 51.6 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Jayhawk](projects/jayhawk.md) | Solar | 101 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Knickerbocker Solar LLC](projects/knickerbocker-solar-llc.md) | Solar | 200 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Kobernat Solar and Storage](projects/kobernat-solar-and-storage.md) | Solar | 100.5 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Lake Whitney Solar](projects/lake-whitney-solar.md) | Solar | 150 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Lampe Solar](projects/lampe-solar.md) | Solar | 60.2 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Langer](projects/langer.md) | Solar | 245 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Lazy U Solar 1](projects/lazy-u-solar-1.md) | Solar | 250 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Lazy U Solar 2](projects/lazy-u-solar-2.md) | Solar | 250 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Leitrim Solar Park](projects/leitrim-solar-park.md) | Solar | 91.2 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Leuven Solar LLC](projects/leuven-solar-llc.md) | Solar | 217 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Liberty Hybrid Solar and Storage Project](projects/liberty-hybrid-solar-and-storage-project.md) | Solar | 67.2 | Houston | **D** | 40 | 80% | 40 | 0 | — |
| [Lupinus Solar 1, LLC](projects/lupinus-solar-1-llc.md) | Solar | 165 | Houston | **D** | 40 | 80% | 40 | 0 | — |
| [Lupinus Solar 2, LLC](projects/lupinus-solar-2-llc.md) | Solar | 244 | Houston | **D** | 40 | 80% | 40 | 0 | — |
| [MRG Goody Solar Project Hybrid](projects/mrg-goody-solar-project-hybrid.md) | Solar | 171.7 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Midpoint Solar, LLC](projects/midpoint-solar-llc.md) | Solar | 97.5 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Musgravite Solar](projects/musgravite-solar.md) | Solar | 100.6 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Myers Solar and Storage](projects/myers-solar-and-storage.md) | Solar | 100.9 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Mystic Springs Renewable Energy (Hybrid)](projects/mystic-springs-renewable-energy-hybrid.md) | Solar | 250 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Nacero Penwell Solar Energy Center](projects/nacero-penwell-solar-energy-center.md) | Solar | 200 | Pan | **D** | 40 | 80% | 40 | 0 | — |
| [Naduah Solar](projects/naduah-solar.md) | Solar | 102.3 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Noor Solar](projects/noor-solar.md) | Solar | 188.2 | Houston | **D** | 40 | 80% | 40 | 0 | — |
| [Noria Hondo Solar (Hybrid)](projects/noria-hondo-solar-hybrid.md) | Solar | 145 | South | **D** | 40 | 80% | 40 | 0 | — |
| [OCI Hillsboro](projects/oci-hillsboro.md) | Solar | 200 | North | **D** | 40 | 80% | 40 | 0 | — |
| [OCI Lone Sun](projects/oci-lone-sun.md) | Solar | 100 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Oak Hill - Dry Creek](projects/oak-hill-dry-creek.md) | Solar | 200 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Old Jackson Solar LLC](projects/old-jackson-solar-llc.md) | Solar | 128 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Orange Grove](projects/orange-grove.md) | Solar | 131 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Oriana Solar LLC](projects/oriana-solar-llc.md) | Solar | 180 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Payne Battlecreek Solar](projects/payne-battlecreek-solar.md) | Solar | 85 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Pecan Prairie South Solar](projects/pecan-prairie-south-solar.md) | Solar | 130 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Peeler Solar, LLC](projects/peeler-solar-llc.md) | Solar | 200 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Pepper Solar Farm](projects/pepper-solar-farm.md) | Solar | 120 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Quarter Ranch Solar](projects/quarter-ranch-solar.md) | Solar | 154.1 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Ratcliff Solar Park](projects/ratcliff-solar-park.md) | Solar | 78.8 | Houston | **D** | 40 | 80% | 40 | 0 | — |
| [Ray Ranch Solar](projects/ray-ranch-solar.md) | Solar | 255.2 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Red Hollow Renewable Energy](projects/red-hollow-renewable-energy.md) | Solar | 300 | North | **D** | 40 | 80% | 40 | 0 | — |
| [RedSun PV and BESS](projects/redsun-pv-and-bess.md) | Solar | 84 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Reis Solar Park](projects/reis-solar-park.md) | Solar | 105.5 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Rio Lago Solar](projects/rio-lago-solar.md) | Solar | 123 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Roaring Springs, LLC](projects/roaring-springs-llc.md) | Solar | 250 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Rockhound A](projects/rockhound-a.md) | Solar | 245 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Rockhound C](projects/rockhound-c.md) | Solar | 61 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Rockhound D](projects/rockhound-d.md) | Solar | 28 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Rosebud Solar, LLC](projects/rosebud-solar-llc.md) | Solar | 132 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Samson Solar Energy](projects/samson-solar-energy.md) | Solar | 250 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Sandford Solar and Storage](projects/sandford-solar-and-storage.md) | Solar | 126.2 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Shakes Solar](projects/shakes-solar.md) | Solar | 200 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Solemio LLC](projects/solemio-llc.md) | Solar | 80 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Stegall Solar and Storage](projects/stegall-solar-and-storage.md) | Solar | 81.6 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Stonewall Solar](projects/stonewall-solar.md) | Solar | 63 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Swift Air Solar II](projects/swift-air-solar-ii.md) | Solar | 100 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Swift Air Solar III](projects/swift-air-solar-iii.md) | Solar | 100 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Symank Solar](projects/symank-solar.md) | Solar | 66.6 | North | **D** | 40 | 80% | 40 | 0 | — |
| [TPE Whitney Solar, LLC](projects/tpe-whitney-solar-llc.md) | Solar | 9.9 | North | **D** | 40 | 80% | 40 | 0 | — |
| [TREX US Red Holly](projects/trex-us-red-holly.md) | Solar | 250 | West | **D** | 40 | 80% | 40 | 0 | — |
| [TX Dallas 7750 Dynasty Drive](projects/tx-dallas-7750-dynasty-drive.md) | Solar | 2.3 | North | **D** | 40 | 80% | 40 | 0 | — |
| [TX LaPort 10060 Porter Road Solar](projects/tx-laport-10060-porter-road-solar.md) | Solar | 1.6 | Houston | **D** | 40 | 80% | 40 | 0 | — |
| [TX Nazareth Solar](projects/tx-nazareth-solar.md) | Solar | 201 | Pan | **D** | 40 | 80% | 40 | 0 | — |
| [TX Pasadena 10585 Red Bluff Road Solar](projects/tx-pasadena-10585-red-bluff-road-solar.md) | Solar | 2.3 | Houston | **D** | 40 | 80% | 40 | 0 | — |
| [TX Sunnyvale 367 Long Creek Road Solar](projects/tx-sunnyvale-367-long-creek-road-solar.md) | Solar | 2 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Timber Cove Solar](projects/timber-cove-solar.md) | Solar | 100 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Tokio Solar](projects/tokio-solar.md) | Solar | 170.4 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Tres Margaritas Solar](projects/tres-margaritas-solar.md) | Solar | 61 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Trinity River Solar 1](projects/trinity-river-solar-1.md) | Solar | 150 | Houston | **D** | 40 | 80% | 40 | 0 | — |
| [Two Rivers Solar](projects/two-rivers-solar.md) | Solar | 214 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Vernon Solar and Storage](projects/vernon-solar-and-storage.md) | Solar | 104 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Wensowitch Solar Park](projects/wensowitch-solar-park.md) | Solar | 145.6 | North | **D** | 40 | 80% | 40 | 0 | — |
| [West of the Pecos Solar](projects/west-of-the-pecos-solar.md) | Solar | 100 | West | **D** | 40 | 80% | 40 | 0 | — |
| [Whistle Solar](projects/whistle-solar.md) | Solar | 56.3 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Wittig Solar Park](projects/wittig-solar-park.md) | Solar | 96 | South | **D** | 40 | 80% | 40 | 0 | — |
| [Yellow Viking Solar](projects/yellow-viking-solar.md) | Solar | 170 | North | **D** | 40 | 80% | 40 | 0 | — |
| [Anacacho Wind Farm, LLC](projects/anacacho-wind-farm-llc.md) | Wind | 99.8 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Astra Wind Farm](projects/astra-wind-farm.md) | Wind | 163 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Baffin Wind](projects/baffin-wind.md) | Wind | 188 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Barton Chapel Wind Farm](projects/barton-chapel-wind-farm.md) | Wind | 120 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [BayWa r.e Mozart LLC](projects/baywa-r-e-mozart-llc.md) | Wind | 30 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Big Sampson Wind](projects/big-sampson-wind.md) | Wind | 265 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Big Spring Wind Power Facility](projects/big-spring-wind-power-facility.md) | Wind | 34.3 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Blackwelder Ranch Wind](projects/blackwelder-ranch-wind.md) | Wind | 110.4 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Blue Summit II Wind](projects/blue-summit-ii-wind.md) | Wind | 99.4 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Blue Summit III Wind](projects/blue-summit-iii-wind.md) | Wind | 200.2 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Blue Summit Wind LLC](projects/blue-summit-wind-llc.md) | Wind | 135.4 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Bluebonnet Prairie Wind](projects/bluebonnet-prairie-wind.md) | Wind | 170 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Bob Creek Wind, LLC](projects/bob-creek-wind-llc.md) | Wind | 175 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Bobcat Bluff Wind Project LLC](projects/bobcat-bluff-wind-project-llc.md) | Wind | 161 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Brazos Wind Farm](projects/brazos-wind-farm.md) | Wind | 182.4 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Briscoe Wind Farm](projects/briscoe-wind-farm.md) | Wind | 150 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Bruennings Breeze Wind Farm](projects/bruennings-breeze-wind-farm.md) | Wind | 228 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Buckthorn Wind](projects/buckthorn-wind.md) | Wind | 100.5 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Buffalo Gap 2 Wind Farm](projects/buffalo-gap-2-wind-farm.md) | Wind | 232.5 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Buffalo Gap 3 Wind Farm](projects/buffalo-gap-3-wind-farm.md) | Wind | 170.2 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Buffalo Gap Wind Farm](projects/buffalo-gap-wind-farm.md) | Wind | 120.6 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Bull Creek Wind](projects/bull-creek-wind.md) | Wind | 180 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Cactus Flats Wind Energy Project](projects/cactus-flats-wind-energy-project.md) | Wind | 148.4 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Callahan Divide Wind Energy Center](projects/callahan-divide-wind-energy-center.md) | Wind | 114 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Canadian Breaks, LLC](projects/canadian-breaks-llc.md) | Wind | 210.1 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Carol Wind, LLC](projects/carol-wind-llc.md) | Wind | 167.8 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Champion Wind Farm LLC](projects/champion-wind-farm-llc.md) | Wind | 126.5 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Changing Winds](projects/changing-winds.md) | Wind | 288 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Chapman Ranch Wind I](projects/chapman-ranch-wind-i.md) | Wind | 236 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Colbeck's Corner, LLC](projects/colbeck-s-corner-llc.md) | Wind | 200 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Cone Renewable Energy Project, LLC](projects/cone-renewable-energy-project-llc.md) | Wind | 150 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Cotton Plains Wind Farm](projects/cotton-plains-wind-farm.md) | Wind | 50.4 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Cranell Wind Farm LLC](projects/cranell-wind-farm-llc.md) | Wind | 220 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Crosby County Wind Farm, LLC](projects/crosby-county-wind-farm-llc.md) | Wind | 150 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Dermott Wind](projects/dermott-wind.md) | Wind | 253 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Desert Sky](projects/desert-sky.md) | Wind | 167.7 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Easter](projects/easter.md) | Wind | 150 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [El Campo Wind](projects/el-campo-wind.md) | Wind | 242.8 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Elbow Creek Wind Project LLC](projects/elbow-creek-wind-project-llc.md) | Wind | 121.9 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Electra Wind Farm](projects/electra-wind-farm.md) | Wind | 230 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Flat Top Wind I](projects/flat-top-wind-i.md) | Wind | 200 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Forest Creek Wind Farm LLC](projects/forest-creek-wind-farm-llc.md) | Wind | 124.2 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Goat Wind LP](projects/goat-wind-lp.md) | Wind | 149.6 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Goldthwaite Wind Energy Facility](projects/goldthwaite-wind-energy-facility.md) | Wind | 150 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Grandview Wind Farm III LLC](projects/grandview-wind-farm-iii-llc.md) | Wind | 188 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Grandview Wind Farm, LLC](projects/grandview-wind-farm-llc.md) | Wind | 211.2 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Green Pastures Wind I](projects/green-pastures-wind-i.md) | Wind | 150 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Green Pastures Wind II](projects/green-pastures-wind-ii.md) | Wind | 150 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Gunsight Mountain Wind Energy LLC](projects/gunsight-mountain-wind-energy-llc.md) | Wind | 120 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Hackberry Wind Farm](projects/hackberry-wind-farm.md) | Wind | 165.6 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Heart of Texas Wind Project](projects/heart-of-texas-wind-project.md) | Wind | 180 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Helena Wind](projects/helena-wind.md) | Wind | 268.2 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Hidalgo Wind Farm II](projects/hidalgo-wind-farm-ii.md) | Wind | 50.4 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Hidalgo Wind Farm LLC](projects/hidalgo-wind-farm-llc.md) | Wind | 250 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Horse Creek Wind Farm](projects/horse-creek-wind-farm.md) | Wind | 230 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Inadale Wind Farm LLC Hybrid](projects/inadale-wind-farm-llc-hybrid.md) | Wind | 197 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Javelina Wind Energy II, LLC](projects/javelina-wind-energy-ii-llc.md) | Wind | 200 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Javelina Wind Energy, LLC](projects/javelina-wind-energy-llc.md) | Wind | 249.7 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Keechi Wind](projects/keechi-wind.md) | Wind | 110 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [King Creek Wind Farm 1, LLC](projects/king-creek-wind-farm-1-llc.md) | Wind | 184 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [King Creek Wind Farm 2, LLC](projects/king-creek-wind-farm-2-llc.md) | Wind | 209 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [King Mountain Wind Ranch 1](projects/king-mountain-wind-ranch-1.md) | Wind | 278 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [La Casa Wind](projects/la-casa-wind.md) | Wind | 150 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Lane City Wind](projects/lane-city-wind.md) | Wind | 202.5 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Langford Wind Power](projects/langford-wind-power.md) | Wind | 150 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Las Lomas Wind Project](projects/las-lomas-wind-project.md) | Wind | 201.6 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Las Majadas Wind Farm](projects/las-majadas-wind-farm.md) | Wind | 272.6 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Limestone Wind Project](projects/limestone-wind-project.md) | Wind | 299.2 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Live Oak Wind Project](projects/live-oak-wind-project.md) | Wind | 199.5 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Logans Gap Wind LLC](projects/logans-gap-wind-llc.md) | Wind | 200.1 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Longhorn Wind](projects/longhorn-wind.md) | Wind | 200 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Loraine Windpark Project LLC](projects/loraine-windpark-project-llc.md) | Wind | 150 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Los Vientos V Wind Power](projects/los-vientos-v-wind-power.md) | Wind | 110 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Los Vientos Wind 1A](projects/los-vientos-wind-1a.md) | Wind | 200 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Los Vientos Wind 1B](projects/los-vientos-wind-1b.md) | Wind | 201.6 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Los Vientos Windpower III](projects/los-vientos-windpower-iii.md) | Wind | 200 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Los Vientos Windpower IV](projects/los-vientos-windpower-iv.md) | Wind | 200 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Magic Valley Wind Farm I LLC](projects/magic-valley-wind-farm-i-llc.md) | Wind | 203 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Majestic 2 Wind Farm](projects/majestic-2-wind-farm.md) | Wind | 79.2 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Mariah del Norte](projects/mariah-del-norte.md) | Wind | 230.4 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [McAdoo Wind Energy LLC](projects/mcadoo-wind-energy-llc.md) | Wind | 150 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [McCrae Wind Energy II](projects/mccrae-wind-energy-ii.md) | Wind | 162.1 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Mesquite Wind Power LLC](projects/mesquite-wind-power-llc.md) | Wind | 200 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Miami Wind Energy Center](projects/miami-wind-energy-center.md) | Wind | 288.6 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Montgomery Ranch Wind Farm, LLC](projects/montgomery-ranch-wind-farm-llc.md) | Wind | 202.5 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [NWP Indian Mesa Wind Farm](projects/nwp-indian-mesa-wind-farm.md) | Wind | 82.5 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Notrees Windpower Hybrid](projects/notrees-windpower-hybrid.md) | Wind | 152.5 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Ocotillo Windpower](projects/ocotillo-windpower.md) | Wind | 58.8 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Old Settler Wind](projects/old-settler-wind.md) | Wind | 151.2 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Panther Creek Wind Farm I](projects/panther-creek-wind-farm-i.md) | Wind | 142.5 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Panther Creek Wind Farm II](projects/panther-creek-wind-farm-ii.md) | Wind | 115.5 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Panther Creek Wind Farm Three](projects/panther-creek-wind-farm-three.md) | Wind | 215.5 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Papalote Creek I LLC](projects/papalote-creek-i-llc.md) | Wind | 180 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Papalote Creek II LLC](projects/papalote-creek-ii-llc.md) | Wind | 200.1 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Patriot Wind Farm](projects/patriot-wind-farm.md) | Wind | 226.1 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Pattern Gulf Wind](projects/pattern-gulf-wind.md) | Wind | 283.2 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Pattern Panhandle Wind 2 LLC](projects/pattern-panhandle-wind-2-llc.md) | Wind | 181.7 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Pattern Panhandle Wind LLC](projects/pattern-panhandle-wind-llc.md) | Wind | 218 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Penascal II Wind Project LLC](projects/penascal-ii-wind-project-llc.md) | Wind | 201.6 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Penascal Wind Power LLC](projects/penascal-wind-power-llc.md) | Wind | 201.6 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Peyton Creek Wind Farm II](projects/peyton-creek-wind-farm-ii.md) | Wind | 243 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Peyton Creek Wind Farm LLC](projects/peyton-creek-wind-farm-llc.md) | Wind | 151.2 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Pioneer Field Wind Power](projects/pioneer-field-wind-power.md) | Wind | 136.2 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Post Oak Wind LLC](projects/post-oak-wind-llc.md) | Wind | 200 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Post Wind Farm LP](projects/post-wind-farm-lp.md) | Wind | 84 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Prairie Switch Wind LLC](projects/prairie-switch-wind-llc.md) | Wind | 163.2 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Pyron Wind Farm LLC Hybrid](projects/pyron-wind-farm-llc-hybrid.md) | Wind | 249 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Quick Draw](projects/quick-draw.md) | Wind | 174 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Ranchland Wind Project I](projects/ranchland-wind-project-i.md) | Wind | 114.9 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Ranchland Wind Project II](projects/ranchland-wind-project-ii.md) | Wind | 148 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Rattlesnake Den](projects/rattlesnake-den.md) | Wind | 207.2 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Rattlesnake Power, LLC](projects/rattlesnake-power-llc.md) | Wind | 160 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Raymond Wind Farm, LLC](projects/raymond-wind-farm-llc.md) | Wind | 200.2 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Raymond, LLC](projects/raymond-llc.md) | Wind | 200 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Rocksprings](projects/rocksprings.md) | Wind | 149.3 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Roscoe Wind Farm LLC](projects/roscoe-wind-farm-llc.md) | Wind | 209 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Route 66 Wind Plant](projects/route-66-wind-plant.md) | Wind | 150 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Salt Fork Wind Project, LLC](projects/salt-fork-wind-project-llc.md) | Wind | 174 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [San Roman Wind I, LLC](projects/san-roman-wind-i-llc.md) | Wind | 95.3 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Sand Bluff Wind Farm](projects/sand-bluff-wind-farm.md) | Wind | 89.5 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Scurry County Wind II](projects/scurry-county-wind-ii.md) | Wind | 120 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Scurry County Wind LP](projects/scurry-county-wind-lp.md) | Wind | 130.5 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Senate Wind LLC](projects/senate-wind-llc.md) | Wind | 150 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Sendero](projects/sendero.md) | Wind | 78 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Seymour Hills Wind Project, LLC](projects/seymour-hills-wind-project-llc.md) | Wind | 30.2 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Shannon Wind](projects/shannon-wind.md) | Wind | 204 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Sherbino II](projects/sherbino-ii.md) | Wind | 132 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Silver Star I Wind Power Project](projects/silver-star-i-wind-power-project.md) | Wind | 52.8 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [South Plains Wind Phase I](projects/south-plains-wind-phase-i.md) | Wind | 200 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [South Trent Wind Farm](projects/south-trent-wind-farm.md) | Wind | 101.2 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Spinning Spur Wind II](projects/spinning-spur-wind-ii.md) | Wind | 161 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Spinning Spur Wind III](projects/spinning-spur-wind-iii.md) | Wind | 194 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Stanton Wind Energy LLC](projects/stanton-wind-energy-llc.md) | Wind | 120 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Stella Wind Farm II](projects/stella-wind-farm-ii.md) | Wind | 200 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Sweetwater Wind 1 LLC](projects/sweetwater-wind-1-llc.md) | Wind | 37.5 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Sweetwater Wind 2 LLC](projects/sweetwater-wind-2-llc.md) | Wind | 98.8 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Sweetwater Wind 3 LLC](projects/sweetwater-wind-3-llc.md) | Wind | 135 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Sweetwater Wind 4 LLC](projects/sweetwater-wind-4-llc.md) | Wind | 241 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Sweetwater Wind 5](projects/sweetwater-wind-5.md) | Wind | 80.5 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [TX Hereford Wind](projects/tx-hereford-wind.md) | Wind | 200 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [TX Jumbo Road Wind](projects/tx-jumbo-road-wind.md) | Wind | 299.7 | Pan | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Tex-Mex Renewable Energy Project, LLC](projects/tex-mex-renewable-energy-project-llc.md) | Wind | 80 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Texas Gulf Wind 2](projects/texas-gulf-wind-2.md) | Wind | 187.2 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Throckmorton Wind](projects/throckmorton-wind.md) | Wind | 225 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Trent Wind Farm](projects/trent-wind-farm.md) | Wind | 152 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Trinity Hills](projects/trinity-hills.md) | Wind | 198 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Turkey Track Wind Energy LLC](projects/turkey-track-wind-energy-llc.md) | Wind | 169.5 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Tyler Bluff Wind Project, LLC](projects/tyler-bluff-wind-project-llc.md) | Wind | 125.6 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Water Valley Wind Energy](projects/water-valley-wind-energy.md) | Wind | 180 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Whirlwind Energy Center](projects/whirlwind-energy-center.md) | Wind | 59.8 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Whitetail](projects/whitetail.md) | Wind | 92 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Wildcat Creek Wind Farm LLC](projects/wildcat-creek-wind-farm-llc.md) | Wind | 175.3 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Willow Beach Wind](projects/willow-beach-wind.md) | Wind | 200 | South | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Willow Springs Wind Farm](projects/willow-springs-wind-farm.md) | Wind | 250 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Windthorst-2](projects/windthorst-2.md) | Wind | 67.6 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Wolf Ridge Wind](projects/wolf-ridge-wind.md) | Wind | 112.5 | North | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Woodward Mountain I](projects/woodward-mountain-i.md) | Wind | 82 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |
| [Woodward Mountain II](projects/woodward-mountain-ii.md) | Wind | 78 | West | **F** | 35.6 | 66.7% | 40 | 0 | — |

## How to improve a project's grade

- **Low completeness** → fill the `missing_fields` listed on the project card, in `ercot_assets.json`.
- **Low verification** → add the project to `eia_sced_crosswalk.csv` and pull its SCED actuals into `plant_sced/plants/`.
- **Low calibration** → run the plant-value pipeline to produce the typical-year gen profile and value parquet.

*Generated by `build_hub.py`.*
