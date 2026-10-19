# FilingIntel benchmark results

Built by `uv run python -m eval.results_table` from the committed files listed below; CI rebuilds it and fails on any difference. Do not edit by hand.

**Every number here is on agent-drafted questions**: 140 questions agents wrote with their gold chunks, not reviewed by a human (`eval/agent_drafted_set.jsonl`). Judged columns come from gpt-6-luna; each column's header says whether it passed calibration against the user's own scores (see Judge calibration).

### Inputs

| file | sha256 | commit |
|---|---|---|
| `benchmarks/calibration/corruption-run1.json` | `941f0a2ee7b85fb08e49435047822104d4af51003b9566f949411e7753f5ac1b` | `8ed7350+dirty` |
| `benchmarks/extraction/0000002488-24-000056-run2.json` | `e6de780b8a651149f62ea76d02d53138ab75480d9c45240ecd64c966355ac7ed` | `67662a2` |
| `benchmarks/extraction/0000002488-24-000123-run2.json` | `27b3ff029c87dfe6fef53ebad7fd4273942f94d38b6a5454d4e462fc2aef6772` | `67662a2` |
| `benchmarks/extraction/0000002488-24-000163-run2.json` | `64eb62dbe97388afcb744ea068d83fbee9a7e9cf348844400522fa4195d20b4d` | `67662a2` |
| `benchmarks/extraction/0000002488-25-000012-run2.json` | `a558c2eacb7a10067d33ea5bc960fcbea666660683f6665fdbe058d1c952f38d` | `67662a2` |
| `benchmarks/extraction/0000002488-25-000047-run2.json` | `86abf95ea1dd2b6e2dd4ce5ed4315993446966db53712fa155099c4437ae8aa1` | `67662a2` |
| `benchmarks/extraction/0000002488-25-000108-run2.json` | `aa9cc3d802893715b2bb846ef294a1c20541441ece25112db7daaa70dbd553c1` | `67662a2` |
| `benchmarks/extraction/0000002488-25-000166-run2.json` | `94e6ebcfd3d81c8b9546ae9a8979e5a94b6a51249a2a045d78eaa488943ecee2` | `67662a2` |
| `benchmarks/extraction/0000002488-26-000018-round1.jsonl` | `ca1f9a1569654d73135c6991f8082bcf27f8164cffa379fabc9547e0e90bae36` | `3fd49ff` |
| `benchmarks/extraction/0000002488-26-000018-run2.json` | `de5ef52ab66b33e18d11b9fb8cd8a7fa1c777a92a46553878de2a92da271a8dd` | `67662a2` |
| `benchmarks/extraction/0000050863-24-000076-run2.json` | `c30592df96d46feed405f63b9da062a5adc72e53403b884772a703cdf7feb25c` | `67662a2` |
| `benchmarks/extraction/0000050863-24-000124-run2.json` | `cbd37e9ac5161c9b9374d48e55b7b5cc687bd5f91367222e2446a7d7a4ddb271` | `67662a2` |
| `benchmarks/extraction/0000050863-24-000149-run2.json` | `4d16b3a9c726f09e635b456eac330b38672df40fc42d0fb541d0ef88ae3d2a9b` | `67662a2` |
| `benchmarks/extraction/0000050863-25-000009-run2.json` | `acefdb8078069f1fb7d885893dbdce0ecae31c7be26f295511d9d30f5864644d` | `67662a2` |
| `benchmarks/extraction/0000050863-25-000074-run2.json` | `d79da354fee0bc139ca5a22cd65c4c7cc8faf7a877848398b15e16b8c8997e1c` | `67662a2` |
| `benchmarks/extraction/0000050863-25-000109-run2.json` | `acd39ea9d78fb50222f5fb6d092451858e11611bd8706622f1c77b6ae05a67c0` | `67662a2` |
| `benchmarks/extraction/0000050863-25-000179-run2.json` | `a7fd6930cf62e5891d948237f48fe9130eff6f317b81a109398148bac9dd0359` | `67662a2` |
| `benchmarks/extraction/0000050863-26-000011-run2.json` | `8c6a421ecac1e0eceafdce17c5cbdf4783d88f6fea6366057dc4fe394530b159` | `67662a2` |
| `benchmarks/extraction/0001045810-24-000124-run2.json` | `9ffa3d9b110bede2ceaee9cae62be9b8a7dc41946927c26dd53e4a4d1f214292` | `67662a2` |
| `benchmarks/extraction/0001045810-24-000264-run2.json` | `ca9ab9cea86a288edbcc3fbae507521c0307af00e5dd7190fe3b155c2c379e28` | `67662a2` |
| `benchmarks/extraction/0001045810-24-000316-run2.json` | `43da7dba6bd7cf642b72d85c04a0b79e2e6c57ca6f51e887b846ef34c0bf86da` | `67662a2` |
| `benchmarks/extraction/0001045810-25-000023-run2.json` | `95cfb227d46cc4dade1120c5546359a1d4564f578248cc9b1ea334aee23786c4` | `67662a2` |
| `benchmarks/extraction/0001045810-25-000116-run2.json` | `fbb14351f367269d9098637edbec4159eac3e7e448c533f2db812edbcaec8781` | `67662a2` |
| `benchmarks/extraction/0001045810-25-000209-run2.json` | `49d9692c8a0bbb84486d7215413c5db6cdcc0a3cfe9c246e5cb3ba3f1792809b` | `67662a2` |
| `benchmarks/extraction/0001045810-25-000230-run2.json` | `4889fd24e4e771c42bb331e7db9360fa41b039fb1fd360210e235d2bf756c908` | `67662a2` |
| `benchmarks/extraction/0001045810-26-000021-round2.jsonl` | `321ddb497cbfae38d0faa904b8caa785c12ddaa45a82fc0fd0aa310134071bce` | `d4cf18e` |
| `benchmarks/extraction/0001045810-26-000021-run3.json` | `2183d4f858bd27a027596b688289e61c563851b891f798519509c726b11b7a65` | `67662a2` |
| `benchmarks/graph/cypher-validity-run1.json` | `7e5e5b345a5174b428f1f1aa8414c409d6c14d3a446519e2a9f4c0e8d05ea382` | `5a1681d` |
| `benchmarks/graph/cypher-validity-run2.json` | `f952e368a3d151ef5c6270677b121c02daf5aad990ea484635c012d628042936` | `e8e8ff3` |
| `benchmarks/graph/global-handoff-run1.json` | `0a7179608e5f0ff26b650bae3ca0efc88e016b528433518ed2d07306b24d1b86` | `b8214f0` |
| `benchmarks/graph/global-provenance-run2.json` | `eb8d92d2808bad574a7e85755874a48d6bde7ec9b516c261324f6798e05ea7d4` | `d2f3fa1` |
| `benchmarks/graph/global-provenance-run3.json` | `0d29e314597ca239383cc6afba61124d3969ca790f531fa9b55e9571984c2c3f` | `4579da0` |
| `benchmarks/persistence/scoring-score1.json` | `c966cca79ce69b187d0710c7ff6971f6ad98fb9119343062db99a695a24d6cfc` | `26f1299` |
| `benchmarks/resolution/scoring-score1.json` | `3c6b056861a9f1f25c6d1f183c500567521c239f5cbe1e76f6b8b9740fad4be8` | `68b35fd` |
| `benchmarks/resolution/scoring2-score1.json` | `ddc25ed65bdb53534e71b225ec0f95d43f60bfc9f1531ee99fa0fb57b6d2c7f3` | `99882ce` |
| `benchmarks/router/dev-best-map.json` | `e7df58c6f9716bc271451c8082decbbbd7cd5c662091cf44097a1a8bc694e865` | `d2f3fa1` |
| `benchmarks/router/route-run1.json` | `e69fe61d728421a8149176f4b5665b9564c9fe068576518c30c4608ded1c1afa` | `b506476` |
| `benchmarks/router/route-run2.json` | `5f1b5dcaf99695ffb7b8a050606c62d9d43ef48a1a047c653a5f42831005a620` | `b506476` |
| `benchmarks/router/route-run3.json` | `11be417b08b3525992872bb61aa29b441c9775fa0103e379dc2605b1cfc864d3` | `b506476` |
| `benchmarks/router/route-run4.json` | `3836cd49e5ec95be57dd8f718bcc7c7e4c9e8f6d4c00acf26bf94ec7721a9ecd` | `fa43bbb` |
| `benchmarks/runs/2026-10-05-vector-dev-96e8c69.json` | `b6aeecc998b924e286f284921b1e7f1eb3262bd4063e8ebba7174f53edd3bb34` | `96e8c69` |
| `benchmarks/runs/2026-10-14-graph_local-dev-375efac.json` | `1e45248f0aaa8c7a2ad81b711210397c83c2e023519ca6ff84854e4b58f22c9b` | `375efac` |
| `benchmarks/runs/2026-10-14-graph_traversal-dev-375efac.json` | `ba11aca73ea858c21746489762b1d1acf2472de46d75fedd0aff9366b2999b13` | `375efac` |
| `benchmarks/runs/2026-10-16-graph_global-dev-5bb0799.json` | `9a221011a43e5ef4dc3efe15e667bdc2799fb7274ca91d28ff5b2ee9818b081e` | `5bb0799` |
| `benchmarks/runs/2026-10-17-graph_global-dev-6e26741.json` | `db093b58149d86f9049f2f391aee789664d5c129ce131f9bacee238f2e180d93` | `6e26741` |
| `benchmarks/runs/2026-10-18-router-dev_best-dev-c1b171d.json` | `256bb9a24d685a223389c3d71ed9b1c5df79d2b87103c323aab9ea59c4890ca6` | `c1b171d` |
| `benchmarks/runs/2026-10-18-router-pre_registered-dev-c1b171d.json` | `47736847e84fc2a6b8b5ea0466591e5e1279168491f58de582e7616481b64488` | `c1b171d` |
| `benchmarks/runs/2026-10-19-graph_global-test-f761581-2.json` | `bcf2733d418ad226fd3153aa79a8f97c2e444b84a2a7e12a2a5572074640e9ce` | `f761581` |
| `benchmarks/runs/2026-10-19-graph_global-test-f761581.json` | `e2705f8ae741a7c46dc868aa730f5dea070fbce7a658f645be47184db28f59e2` | `f761581` |
| `benchmarks/runs/2026-10-19-graph_local-test-f761581.json` | `845130feab9d07e2b8bc3cdee72458f8e77df55de0aaf3a9218c8761a45f6cc9` | `f761581` |
| `benchmarks/runs/2026-10-19-graph_traversal-test-f761581.json` | `a41f1746c4a1292986f3ce02631ac8fab3a892d2eee0b4c9212218ebace8f95a` | `f761581` |
| `benchmarks/runs/2026-10-19-router-dev_best-test-f761581.json` | `74260cd3fce2e4556408876c6575f14c0a782707c944d6fc7a52078b5e679b62` | `f761581` |
| `benchmarks/runs/2026-10-19-router-pre_registered-test-f761581.json` | `a713ce16bebbf89775282e5ace3774a2cac79729d76fb29ca0aa221ada504d78` | `f761581` |
| `benchmarks/runs/2026-10-19-vector-test-f761581.json` | `4ec06f122d6fa93eb0cd4d6171324f67c71911f3369fc057fe3d236794d8260e` | `f761581` |

## `test` results

The router rows are not arms: the router classifies each question with claude-haiku-5-5, declines investment advice itself and hands the rest to the arm its routing map names. The dev-best map is tuned on dev.

Answerable classes. Intervals are percentile bootstrap 95% over the cell's questions; a cell under 10 questions is directional.

| class | arm | n | recall@5 | recall@10 | precision@10 | wrong evidence | faithfulness (uncalibrated) | answer relevancy (uncalibrated) | citation support (uncalibrated) | p50 total ms | p95 total ms | cost / question (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| lookup | vector | 13 | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.100 [0.100, 0.100] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.917 [0.870, 0.952] | 0.955 [0.891, 1.000] | 3,398 [2,458, 4,512] | 6,505 [4,053, 6,561] | 0.0267 [0.0253, 0.0286] |
| lookup | graph_local | 13 | 0.923 [0.769, 1.000] | 1.000 [1.000, 1.000] | 0.100 [0.100, 0.100] | 0.000 [0.000, 0.000] | 0.996 [0.987, 1.000] | 0.911 [0.870, 0.945] | 0.979 [0.949, 1.000] | 2,787 [2,371, 3,968] (13 replayed) | 6,399 [3,774, 6,400] | 0.0259 [0.0244, 0.0279] |
| lookup | graph_traversal | 13 | 0.923 [0.769, 1.000] | 0.923 [0.769, 1.000] | 0.092 [0.077, 0.100] | 0.000 [0.000, 0.000] | 0.949 [0.846, 1.000] | 0.873 [0.787, 0.941] | 0.972 [0.921, 1.000] | 8,644 [8,135, 9,563] (13 replayed) | 13,406 [9,563, 13,785] | 0.0220 [0.0181, 0.0265] |
| lookup | graph_global | 13 | 0.308 [0.077, 0.538] | 0.308 [0.077, 0.538] | 0.031 [0.008, 0.054] | 0.000 [0.000, 0.000] (ranks only) | retrieval only | retrieval only | retrieval only | retrieval only | retrieval only | retrieval only |
| lookup | router (pre-registered) | 13 | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.100 [0.100, 0.100] | 0.000 [0.000, 0.000] | 1.000 [1.000, 1.000] | 0.918 [0.875, 0.952] | 0.955 [0.891, 1.000] | 1,464 [1,351, 1,582] (13 replayed) | 1,777 [1,570, 1,915] | 0.0268 [0.0253, 0.0286] |
| lookup | router (dev-best) | 13 | 0.923 [0.769, 1.000] | 1.000 [1.000, 1.000] | 0.100 [0.100, 0.100] | 0.000 [0.000, 0.000] | 0.996 [0.987, 1.000] | 0.911 [0.869, 0.945] | 0.979 [0.949, 1.000] | 115 [103, 121] (13 replayed) | 150 [121, 178] | 0.0260 [0.0245, 0.0279] |
| local | vector | 13 | 0.923 [0.808, 1.000] | 0.962 [0.885, 1.000] | 0.115 [0.100, 0.138] | 0.000 [0.000, 0.000] | 0.995 [0.988, 1.000] | 0.776 [0.729, 0.816] | 0.980 [0.959, 0.998] | 6,765 [4,602, 7,856] | 10,031 [7,772, 12,217] | 0.0332 [0.0317, 0.0347] |
| local | graph_local | 13 | 0.385 [0.154, 0.615] | 0.500 [0.231, 0.769] | 0.062 [0.031, 0.100] | 0.077 [0.000, 0.231] | 0.977 [0.959, 0.993] | 0.674 [0.533, 0.782] | 0.982 [0.958, 1.000] | 6,752 [4,175, 7,914] (13 replayed) | 9,930 [7,914, 9,974] | 0.0322 [0.0303, 0.0342] |
| local | graph_traversal | 13 | 0.654 [0.423, 0.846] | 0.731 [0.500, 0.923] | 0.092 [0.062, 0.123] | 0.000 [0.000, 0.000] | 0.980 [0.958, 0.995] | 0.750 [0.700, 0.801] | 0.975 [0.954, 0.992] | 15,867 [10,624, 20,830] (13 replayed) | 21,620 [18,325, 22,299] | 0.0357 [0.0298, 0.0413] |
| local | graph_global | 13 | 0.423 [0.192, 0.692] | 0.654 [0.423, 0.846] | 0.077 [0.054, 0.100] | 0.077 [0.000, 0.231] (ranks only) | retrieval only | retrieval only | retrieval only | retrieval only | retrieval only | retrieval only |
| local | router (pre-registered) | 13 | 0.385 [0.154, 0.615] | 0.500 [0.231, 0.769] | 0.062 [0.031, 0.092] | 0.077 [0.000, 0.231] | 0.976 [0.959, 0.991] | 0.669 [0.533, 0.777] | 0.982 [0.956, 1.000] | 1,995 [1,695, 2,550] (13 replayed) | 6,603 [2,465, 7,791] | 0.0338 [0.0307, 0.0380] |
| local | router (dev-best) | 13 | 0.385 [0.154, 0.615] | 0.500 [0.231, 0.769] | 0.062 [0.031, 0.092] | 0.077 [0.000, 0.231] | 0.977 [0.959, 0.993] | 0.674 [0.536, 0.782] | 0.982 [0.956, 1.000] | 223 [172, 637] (13 replayed) | 1,530 [511, 2,570] | 0.0323 [0.0305, 0.0342] |
| multi_hop | vector | 7 (directional) | 0.536 [0.250, 0.821] | 0.798 [0.595, 0.964] | 0.186 [0.143, 0.229] | 0.429 [0.143, 0.857] | 0.972 [0.915, 1.000] | 0.840 [0.792, 0.885] | 1.000 [1.000, 1.000] | 5,055 [4,358, 9,798] | 11,017 [6,925, 11,539] | 0.0322 [0.0294, 0.0349] |
| multi_hop | graph_local | 7 (directional) | 0.429 [0.214, 0.643] | 0.500 [0.214, 0.786] | 0.114 [0.057, 0.171] | 0.429 [0.143, 0.857] | 0.854 [0.702, 0.991] | 0.816 [0.757, 0.875] | 0.948 [0.901, 0.986] | 7,268 [5,370, 8,197] (7 replayed) | 9,652 [7,314, 10,275] | 0.0301 [0.0272, 0.0333] |
| multi_hop | graph_traversal | 7 (directional) | 0.488 [0.274, 0.702] | 0.524 [0.310, 0.714] | 0.129 [0.071, 0.171] | 0.143 [0.000, 0.429] | 0.986 [0.959, 1.000] | 0.818 [0.746, 0.876] | 1.000 [1.000, 1.000] | 10,364 [9,612, 13,424] (7 replayed) | 23,497 [11,346, 27,814] | 0.0336 [0.0247, 0.0439] |
| multi_hop | graph_global | 7 (directional) | 0.357 [0.071, 0.714] | 0.357 [0.071, 0.714] | 0.071 [0.014, 0.143] | 0.143 [0.000, 0.429] (ranks only) | retrieval only | retrieval only | retrieval only | retrieval only | retrieval only | retrieval only |
| multi_hop | router (pre-registered) | 7 (directional) | 0.488 [0.274, 0.702] | 0.524 [0.310, 0.738] | 0.129 [0.071, 0.171] | 0.143 [0.000, 0.429] | 0.986 [0.959, 1.000] | 0.818 [0.744, 0.877] | 1.000 [1.000, 1.000] | 1,816 [1,601, 2,581] (7 replayed) | 2,793 [1,938, 2,883] | 0.0337 [0.0248, 0.0440] |
| multi_hop | router (dev-best) | 7 (directional) | 0.429 [0.214, 0.643] | 0.500 [0.214, 0.786] | 0.114 [0.057, 0.171] | 0.429 [0.143, 0.857] | 0.854 [0.702, 0.991] | 0.816 [0.758, 0.875] | 0.948 [0.901, 0.986] | 100 [94, 118] (7 replayed) | 139 [101, 148] | 0.0302 [0.0273, 0.0334] |
| global | vector | 5 (directional) | 0.667 [0.467, 0.867] | 0.800 [0.667, 0.933] | 0.240 [0.200, 0.280] | 0.600 [0.200, 1.000] | 0.743 [0.532, 0.954] | 0.750 [0.675, 0.826] | 0.930 [0.815, 1.000] | 8,095 [7,508, 14,408] | 13,597 [8,036, 14,408] | 0.0368 [0.0334, 0.0404] |
| global | graph_local | 5 (directional) | 0.067 [0.000, 0.200] | 0.067 [0.000, 0.200] | 0.020 [0.000, 0.060] | 0.200 [0.000, 0.600] | 0.677 [0.441, 0.913] | 0.557 [0.261, 0.777] | 0.852 [0.585, 1.000] | 7,486 [5,434, 9,900] (5 replayed) | 9,828 [7,337, 9,900] | 0.0348 [0.0326, 0.0370] |
| global | graph_traversal | 5 (directional) | 0.133 [0.000, 0.400] | 0.267 [0.000, 0.667] | 0.080 [0.000, 0.200] | 0.400 [0.000, 0.800] | 0.944 [0.894, 0.991] | 0.711 [0.610, 0.823] | 0.944 [0.884, 1.000] | 15,180 [9,963, 46,111] (5 replayed) | 40,321 [14,772, 46,111] | 0.0423 [0.0299, 0.0550] |
| global | graph_global | 5 (directional) | 0.200 [0.067, 0.333] | 0.333 [0.067, 0.600] | 0.100 [0.020, 0.180] | 0.200 [0.000, 0.600] | 0.717 [0.471, 0.941] | 0.755 [0.678, 0.818] | 0.750 [0.597, 0.885] | 12,764 [10,189, 15,454] (5 replayed) | 15,036 [12,550, 15,454] | 0.0913 [0.0852, 0.0975] |
| global | router (pre-registered) | 5 (directional) | 0.200 [0.067, 0.333] | 0.333 [0.067, 0.600] | 0.100 [0.020, 0.180] | 0.200 [0.000, 0.600] | 0.717 [0.471, 0.941] | 0.755 [0.678, 0.818] | 0.750 [0.597, 0.885] | 1,459 [1,194, 3,787] (5 replayed) | 3,514 [1,444, 3,787] | 0.0914 [0.0853, 0.0975] |
| global | router (dev-best) | 5 (directional) | 0.667 [0.467, 0.867] | 0.800 [0.667, 0.933] | 0.240 [0.200, 0.280] | 0.600 [0.200, 1.000] | 0.743 [0.532, 0.954] | 0.750 [0.675, 0.826] | 0.930 [0.815, 1.000] | 43 [37, 223] (5 replayed) | 187 [42, 223] | 0.0369 [0.0337, 0.0405] |

Decline and unanswerable questions: whether the answer declined or said the filings lack the evidence.

| class | arm | n | correct (uncalibrated) | p50 total ms | p95 total ms | cost / question (USD) |
| --- | --- | --- | --- | --- | --- | --- |
| decline | vector | 4 (directional) | 1.000 [1.000, 1.000] | 2,364 [1,317, 2,798] | 2,772 [2,106, 2,798] | 0.0261 [0.0252, 0.0269] |
| decline | graph_local | 4 (directional) | 1.000 [1.000, 1.000] | 2,982 [2,061, 4,873] (4 replayed) | 4,688 [2,326, 4,873] | 0.0239 [0.0218, 0.0259] |
| decline | graph_traversal | 4 (directional) | 1.000 [1.000, 1.000] | 8,240 [7,083, 12,360] (4 replayed) | 11,809 [7,794, 12,360] | 0.0387 [0.0322, 0.0490] |
| decline | graph_global | 4 (directional) | 1.000 [1.000, 1.000] | 8,786 [6,350, 9,569] (4 replayed) | 9,506 [8,422, 9,569] | 0.0711 [0.0628, 0.0774] |
| decline | router (pre-registered) | 4 (directional) | 1.000 [1.000, 1.000] | 1,588 [1,185, 2,834] | 2,689 [1,304, 2,834] | 0.0001 [0.0001, 0.0002] |
| decline | router (dev-best) | 4 (directional) | 1.000 [1.000, 1.000] | 1 [1, 9] (4 replayed) | 8 [1, 9] | 0.0001 [0.0001, 0.0002] |
| unanswerable | vector | 5 (directional) | 1.000 [1.000, 1.000] | 2,574 [2,162, 3,999] | 3,914 [2,525, 3,999] | 0.0277 [0.0266, 0.0287] |
| unanswerable | graph_local | 5 (directional) | 1.000 [1.000, 1.000] | 2,363 [1,944, 3,200] (5 replayed) | 3,159 [2,343, 3,200] | 0.0246 [0.0224, 0.0273] |
| unanswerable | graph_traversal | 5 (directional) | 1.000 [1.000, 1.000] | 9,982 [8,191, 17,632] (5 replayed) | 16,917 [9,882, 17,632] | 0.0338 [0.0209, 0.0467] |
| unanswerable | graph_global | 5 (directional) | 1.000 [1.000, 1.000] | 7,816 [7,109, 10,595] (5 replayed) | 10,434 [7,789, 10,595] | 0.0644 [0.0546, 0.0727] |
| unanswerable | router (pre-registered) | 5 (directional) | 1.000 [1.000, 1.000] | 1,782 [1,303, 4,065] (5 replayed) | 3,631 [1,714, 4,065] | 0.0277 [0.0267, 0.0288] |
| unanswerable | router (dev-best) | 5 (directional) | 1.000 [1.000, 1.000] | 151 [121, 314] (5 replayed) | 282 [147, 314] | 0.0247 [0.0225, 0.0274] |

### `test` paired differences against `vector`

On the questions both runs hold, as `eval.compare` computes them; a difference counts only when its interval excludes 0. graph_global's judged differences compare whole pipelines (its reduce prompt is not `answer/v1`).

| arm | class | metric | n | difference (arm - vector) [95%] | counts | note |
| --- | --- | --- | --- | --- | --- | --- |
| graph_local | lookup | recall@5 | 13 | -0.077 [-0.231, 0.000] | no | - |
| graph_local | lookup | recall@10 | 13 | 0.000 [0.000, 0.000] | no | - |
| graph_local | lookup | precision@10 | 13 | 0.000 [0.000, 0.000] | no | - |
| graph_local | lookup | wrong evidence | 13 | 0.000 [0.000, 0.000] | no | - |
| graph_local | lookup | faithfulness | 13 | -0.004 [-0.013, 0.000] | no | uncalibrated |
| graph_local | lookup | answer relevancy | 13 | -0.006 [-0.024, 0.006] | no | uncalibrated |
| graph_local | lookup | citation support | 13 | 0.024 [-0.038, 0.096] | no | uncalibrated |
| graph_local | local | recall@5 | 13 | -0.538 [-0.769, -0.308] | yes | - |
| graph_local | local | recall@10 | 13 | -0.462 [-0.731, -0.231] | yes | - |
| graph_local | local | precision@10 | 13 | -0.054 [-0.077, -0.023] | yes | - |
| graph_local | local | wrong evidence | 13 | 0.077 [0.000, 0.231] | no | - |
| graph_local | local | faithfulness | 13 | -0.018 [-0.033, -0.006] | yes | uncalibrated |
| graph_local | local | answer relevancy | 13 | -0.101 [-0.228, -0.008] | yes | uncalibrated |
| graph_local | local | citation support | 13 | 0.002 [-0.031, 0.033] | no | uncalibrated |
| graph_local | multi_hop | recall@5 | 7 | -0.107 [-0.464, 0.179] | no | directional |
| graph_local | multi_hop | recall@10 | 7 | -0.298 [-0.571, -0.071] | yes | directional |
| graph_local | multi_hop | precision@10 | 7 | -0.071 [-0.129, -0.029] | yes | directional |
| graph_local | multi_hop | wrong evidence | 7 | 0.000 [-0.429, 0.429] | no | directional |
| graph_local | multi_hop | faithfulness | 7 | -0.117 [-0.281, 0.039] | no | directional, uncalibrated |
| graph_local | multi_hop | answer relevancy | 7 | -0.024 [-0.047, 0.000] | no | directional, uncalibrated |
| graph_local | multi_hop | citation support | 7 | -0.052 [-0.097, -0.014] | yes | directional, uncalibrated |
| graph_local | global | recall@5 | 5 | -0.600 [-0.667, -0.467] | yes | directional |
| graph_local | global | recall@10 | 5 | -0.733 [-0.867, -0.667] | yes | directional |
| graph_local | global | precision@10 | 5 | -0.220 [-0.260, -0.200] | yes | directional |
| graph_local | global | wrong evidence | 5 | -0.400 [-0.800, 0.000] | no | directional |
| graph_local | global | faithfulness | 5 | -0.066 [-0.235, 0.094] | no | directional, uncalibrated |
| graph_local | global | answer relevancy | 5 | -0.193 [-0.423, -0.041] | yes | directional, uncalibrated |
| graph_local | global | citation support | 5 | -0.078 [-0.230, 0.027] | no | directional, uncalibrated |
| graph_local | pooled | recall@5 | 38 | -0.309 [-0.447, -0.175] | yes | - |
| graph_local | pooled | recall@10 | 38 | -0.309 [-0.439, -0.184] | yes | - |
| graph_local | pooled | precision@10 | 38 | -0.061 [-0.087, -0.037] | yes | - |
| graph_local | pooled | wrong evidence | 38 | -0.026 [-0.132, 0.079] | no | - |
| graph_local | pooled | faithfulness | 38 | -0.038 [-0.082, -0.001] | yes | uncalibrated |
| graph_local | pooled | answer relevancy | 38 | -0.067 [-0.123, -0.022] | yes | uncalibrated |
| graph_local | pooled | citation support | 38 | -0.011 [-0.045, 0.025] | no | uncalibrated |
| graph_local | decline | decline correct | 4 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| graph_local | unanswerable | not found correct | 5 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| graph_traversal | lookup | recall@5 | 13 | -0.077 [-0.231, 0.000] | no | - |
| graph_traversal | lookup | recall@10 | 13 | -0.077 [-0.231, 0.000] | no | - |
| graph_traversal | lookup | precision@10 | 13 | -0.008 [-0.023, 0.000] | no | - |
| graph_traversal | lookup | wrong evidence | 13 | 0.000 [0.000, 0.000] | no | - |
| graph_traversal | lookup | faithfulness | 13 | -0.051 [-0.154, 0.000] | no | uncalibrated |
| graph_traversal | lookup | answer relevancy | 13 | -0.045 [-0.120, 0.004] | no | uncalibrated |
| graph_traversal | lookup | citation support | 13 | 0.017 [-0.060, 0.096] | no | uncalibrated |
| graph_traversal | local | recall@5 | 13 | -0.269 [-0.500, -0.077] | yes | - |
| graph_traversal | local | recall@10 | 13 | -0.231 [-0.462, 0.000] | no | - |
| graph_traversal | local | precision@10 | 13 | -0.023 [-0.046, 0.000] | no | - |
| graph_traversal | local | wrong evidence | 13 | 0.000 [0.000, 0.000] | no | - |
| graph_traversal | local | faithfulness | 13 | -0.015 [-0.037, -0.001] | yes | uncalibrated |
| graph_traversal | local | answer relevancy | 13 | -0.025 [-0.062, 0.006] | no | uncalibrated |
| graph_traversal | local | citation support | 13 | -0.006 [-0.033, 0.025] | no | uncalibrated |
| graph_traversal | multi_hop | recall@5 | 7 | -0.048 [-0.286, 0.214] | no | directional |
| graph_traversal | multi_hop | recall@10 | 7 | -0.274 [-0.571, 0.012] | no | directional |
| graph_traversal | multi_hop | precision@10 | 7 | -0.057 [-0.129, 0.014] | no | directional |
| graph_traversal | multi_hop | wrong evidence | 7 | -0.286 [-0.571, 0.000] | no | directional |
| graph_traversal | multi_hop | faithfulness | 7 | 0.015 [0.000, 0.044] | no | directional, uncalibrated |
| graph_traversal | multi_hop | answer relevancy | 7 | -0.022 [-0.055, 0.013] | no | directional, uncalibrated |
| graph_traversal | multi_hop | citation support | 7 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| graph_traversal | global | recall@5 | 5 | -0.533 [-0.667, -0.400] | yes | directional |
| graph_traversal | global | recall@10 | 5 | -0.533 [-0.800, -0.267] | yes | directional |
| graph_traversal | global | precision@10 | 5 | -0.160 [-0.240, -0.080] | yes | directional |
| graph_traversal | global | wrong evidence | 5 | -0.200 [-0.600, 0.000] | no | directional |
| graph_traversal | global | faithfulness | 5 | 0.201 [-0.009, 0.420] | no | directional, uncalibrated |
| graph_traversal | global | answer relevancy | 5 | -0.040 [-0.163, 0.046] | no | directional, uncalibrated |
| graph_traversal | global | citation support | 5 | 0.014 [-0.089, 0.158] | no | directional, uncalibrated |
| graph_traversal | pooled | recall@5 | 38 | -0.197 [-0.316, -0.083] | yes | - |
| graph_traversal | pooled | recall@10 | 38 | -0.226 [-0.351, -0.110] | yes | - |
| graph_traversal | pooled | precision@10 | 38 | -0.042 [-0.068, -0.018] | yes | - |
| graph_traversal | pooled | wrong evidence | 38 | -0.079 [-0.184, 0.000] | no | - |
| graph_traversal | pooled | faithfulness | 38 | 0.006 [-0.046, 0.058] | no | uncalibrated |
| graph_traversal | pooled | answer relevancy | 38 | -0.033 [-0.067, -0.006] | yes | uncalibrated |
| graph_traversal | pooled | citation support | 38 | 0.006 [-0.027, 0.041] | no | uncalibrated |
| graph_traversal | decline | decline correct | 4 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| graph_traversal | unanswerable | not found correct | 5 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| graph_global | lookup | recall@5 | 13 | -0.692 [-0.923, -0.462] | yes | - |
| graph_global | lookup | recall@10 | 13 | -0.692 [-0.923, -0.462] | yes | - |
| graph_global | lookup | precision@10 | 13 | -0.069 [-0.092, -0.046] | yes | - |
| graph_global | lookup | wrong evidence | 13 | 0.000 [0.000, 0.000] | no | - |
| graph_global | local | recall@5 | 13 | -0.500 [-0.731, -0.269] | yes | - |
| graph_global | local | recall@10 | 13 | -0.308 [-0.538, -0.077] | yes | - |
| graph_global | local | precision@10 | 13 | -0.038 [-0.069, -0.015] | yes | - |
| graph_global | local | wrong evidence | 13 | 0.077 [0.000, 0.231] | no | - |
| graph_global | multi_hop | recall@5 | 7 | -0.179 [-0.571, 0.214] | no | directional |
| graph_global | multi_hop | recall@10 | 7 | -0.440 [-0.702, -0.190] | yes | directional |
| graph_global | multi_hop | precision@10 | 7 | -0.114 [-0.186, -0.043] | yes | directional |
| graph_global | multi_hop | wrong evidence | 7 | -0.286 [-0.571, 0.000] | no | directional |
| graph_global | global | recall@5 | 5 | -0.467 [-0.667, -0.200] | yes | directional |
| graph_global | global | recall@10 | 5 | -0.467 [-0.667, -0.200] | yes | directional |
| graph_global | global | precision@10 | 5 | -0.140 [-0.200, -0.060] | yes | directional |
| graph_global | global | wrong evidence | 5 | -0.400 [-0.800, 0.000] | no | directional |
| graph_global | global | faithfulness | 5 | -0.026 [-0.154, 0.083] | no | directional, uncalibrated |
| graph_global | global | answer relevancy | 5 | 0.004 [-0.087, 0.096] | no | directional, uncalibrated |
| graph_global | global | citation support | 5 | -0.180 [-0.244, -0.113] | yes | directional, uncalibrated |
| graph_global | decline | decline correct | 4 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| graph_global | unanswerable | not found correct | 5 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| router (pre-registered) | lookup | recall@5 | 13 | 0.000 [0.000, 0.000] | no | - |
| router (pre-registered) | lookup | recall@10 | 13 | 0.000 [0.000, 0.000] | no | - |
| router (pre-registered) | lookup | precision@10 | 13 | 0.000 [0.000, 0.000] | no | - |
| router (pre-registered) | lookup | wrong evidence | 13 | 0.000 [0.000, 0.000] | no | - |
| router (pre-registered) | lookup | faithfulness | 13 | 0.000 [0.000, 0.000] | no | uncalibrated |
| router (pre-registered) | lookup | answer relevancy | 13 | 0.001 [0.000, 0.004] | no | uncalibrated |
| router (pre-registered) | lookup | citation support | 13 | 0.000 [0.000, 0.000] | no | uncalibrated |
| router (pre-registered) | local | recall@5 | 13 | -0.538 [-0.769, -0.308] | yes | - |
| router (pre-registered) | local | recall@10 | 13 | -0.462 [-0.731, -0.231] | yes | - |
| router (pre-registered) | local | precision@10 | 13 | -0.054 [-0.077, -0.023] | yes | - |
| router (pre-registered) | local | wrong evidence | 13 | 0.077 [0.000, 0.231] | no | - |
| router (pre-registered) | local | faithfulness | 13 | -0.019 [-0.033, -0.007] | yes | uncalibrated |
| router (pre-registered) | local | answer relevancy | 13 | -0.106 [-0.232, -0.014] | yes | uncalibrated |
| router (pre-registered) | local | citation support | 13 | 0.002 [-0.031, 0.033] | no | uncalibrated |
| router (pre-registered) | multi_hop | recall@5 | 7 | -0.048 [-0.286, 0.214] | no | directional |
| router (pre-registered) | multi_hop | recall@10 | 7 | -0.274 [-0.571, 0.012] | no | directional |
| router (pre-registered) | multi_hop | precision@10 | 7 | -0.057 [-0.129, 0.014] | no | directional |
| router (pre-registered) | multi_hop | wrong evidence | 7 | -0.286 [-0.571, 0.000] | no | directional |
| router (pre-registered) | multi_hop | faithfulness | 7 | 0.015 [0.000, 0.044] | no | directional, uncalibrated |
| router (pre-registered) | multi_hop | answer relevancy | 7 | -0.022 [-0.055, 0.013] | no | directional, uncalibrated |
| router (pre-registered) | multi_hop | citation support | 7 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| router (pre-registered) | global | recall@5 | 5 | -0.467 [-0.667, -0.200] | yes | directional |
| router (pre-registered) | global | recall@10 | 5 | -0.467 [-0.667, -0.200] | yes | directional |
| router (pre-registered) | global | precision@10 | 5 | -0.140 [-0.200, -0.060] | yes | directional |
| router (pre-registered) | global | wrong evidence | 5 | -0.400 [-0.800, 0.000] | no | directional |
| router (pre-registered) | global | faithfulness | 5 | -0.026 [-0.154, 0.083] | no | directional, uncalibrated |
| router (pre-registered) | global | answer relevancy | 5 | 0.004 [-0.087, 0.096] | no | directional, uncalibrated |
| router (pre-registered) | global | citation support | 5 | -0.180 [-0.244, -0.113] | yes | directional, uncalibrated |
| router (pre-registered) | pooled | recall@5 | 38 | -0.254 [-0.390, -0.127] | yes | - |
| router (pre-registered) | pooled | recall@10 | 38 | -0.270 [-0.399, -0.149] | yes | - |
| router (pre-registered) | pooled | precision@10 | 38 | -0.047 [-0.071, -0.026] | yes | - |
| router (pre-registered) | pooled | wrong evidence | 38 | -0.079 [-0.184, 0.026] | no | - |
| router (pre-registered) | pooled | faithfulness | 38 | -0.007 [-0.025, 0.009] | no | uncalibrated |
| router (pre-registered) | pooled | answer relevancy | 38 | -0.039 [-0.088, -0.002] | yes | uncalibrated |
| router (pre-registered) | pooled | citation support | 38 | -0.023 [-0.049, -0.001] | yes | uncalibrated |
| router (pre-registered) | decline | decline correct | 4 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| router (pre-registered) | unanswerable | not found correct | 5 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| router (dev-best) | lookup | recall@5 | 13 | -0.077 [-0.231, 0.000] | no | - |
| router (dev-best) | lookup | recall@10 | 13 | 0.000 [0.000, 0.000] | no | - |
| router (dev-best) | lookup | precision@10 | 13 | 0.000 [0.000, 0.000] | no | - |
| router (dev-best) | lookup | wrong evidence | 13 | 0.000 [0.000, 0.000] | no | - |
| router (dev-best) | lookup | faithfulness | 13 | -0.004 [-0.013, 0.000] | no | uncalibrated |
| router (dev-best) | lookup | answer relevancy | 13 | -0.006 [-0.024, 0.006] | no | uncalibrated |
| router (dev-best) | lookup | citation support | 13 | 0.024 [-0.038, 0.096] | no | uncalibrated |
| router (dev-best) | local | recall@5 | 13 | -0.538 [-0.769, -0.308] | yes | - |
| router (dev-best) | local | recall@10 | 13 | -0.462 [-0.731, -0.231] | yes | - |
| router (dev-best) | local | precision@10 | 13 | -0.054 [-0.077, -0.023] | yes | - |
| router (dev-best) | local | wrong evidence | 13 | 0.077 [0.000, 0.231] | no | - |
| router (dev-best) | local | faithfulness | 13 | -0.018 [-0.033, -0.006] | yes | uncalibrated |
| router (dev-best) | local | answer relevancy | 13 | -0.101 [-0.228, -0.008] | yes | uncalibrated |
| router (dev-best) | local | citation support | 13 | 0.002 [-0.031, 0.033] | no | uncalibrated |
| router (dev-best) | multi_hop | recall@5 | 7 | -0.107 [-0.464, 0.179] | no | directional |
| router (dev-best) | multi_hop | recall@10 | 7 | -0.298 [-0.571, -0.071] | yes | directional |
| router (dev-best) | multi_hop | precision@10 | 7 | -0.071 [-0.129, -0.029] | yes | directional |
| router (dev-best) | multi_hop | wrong evidence | 7 | 0.000 [-0.429, 0.429] | no | directional |
| router (dev-best) | multi_hop | faithfulness | 7 | -0.117 [-0.281, 0.039] | no | directional, uncalibrated |
| router (dev-best) | multi_hop | answer relevancy | 7 | -0.024 [-0.047, 0.000] | no | directional, uncalibrated |
| router (dev-best) | multi_hop | citation support | 7 | -0.052 [-0.097, -0.014] | yes | directional, uncalibrated |
| router (dev-best) | global | recall@5 | 5 | 0.000 [0.000, 0.000] | no | directional |
| router (dev-best) | global | recall@10 | 5 | 0.000 [0.000, 0.000] | no | directional |
| router (dev-best) | global | precision@10 | 5 | 0.000 [0.000, 0.000] | no | directional |
| router (dev-best) | global | wrong evidence | 5 | 0.000 [0.000, 0.000] | no | directional |
| router (dev-best) | global | faithfulness | 5 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| router (dev-best) | global | answer relevancy | 5 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| router (dev-best) | global | citation support | 5 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| router (dev-best) | pooled | recall@5 | 38 | -0.230 [-0.368, -0.105] | yes | - |
| router (dev-best) | pooled | recall@10 | 38 | -0.213 [-0.336, -0.101] | yes | - |
| router (dev-best) | pooled | precision@10 | 38 | -0.032 [-0.050, -0.016] | yes | - |
| router (dev-best) | pooled | wrong evidence | 38 | 0.026 [-0.053, 0.105] | no | - |
| router (dev-best) | pooled | faithfulness | 38 | -0.029 [-0.069, -0.001] | yes | uncalibrated |
| router (dev-best) | pooled | answer relevancy | 38 | -0.041 [-0.088, -0.007] | yes | uncalibrated |
| router (dev-best) | pooled | citation support | 38 | -0.001 [-0.027, 0.029] | no | uncalibrated |
| router (dev-best) | decline | decline correct | 4 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| router (dev-best) | unanswerable | not found correct | 5 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |

## Hypotheses (read on `test`)

H6 reads the pre-registered router's `dev` run, as the plan writes it.

- **supported**: H1, vector holds on lookups: on lookup, for every graph arm, the mean paired difference (graph - vector) in recall@5 is at most 0.05.
  - graph_local - vector: mean -0.077 [-0.231, +0.000], n 13
  - graph_traversal - vector: mean -0.077 [-0.231, +0.000], n 13
  - graph_global - vector: mean -0.692 [-0.923, -0.462], n 13
- **directional, rule not met**: H2, traversal helps on joins: on multi_hop, the mean paired difference (graph_traversal - vector) in recall@10 is at least 0.1 and its 95% interval excludes 0.
  - graph_traversal - vector: mean -0.274 [-0.571, +0.012], n 7
- **not supported**: H3, period confusion is the main failure: for every arm, more than half of the answerable questions with wrong evidence have a same_company_other_period hard negative among their hits; an arm with no wrong evidence is listed and skipped.
  - vector: 4 of 6 wrong-evidence questions involve same_company_other_period
  - graph_local: 3 of 5 wrong-evidence questions involve same_company_other_period
  - graph_traversal: 2 of 3 wrong-evidence questions involve same_company_other_period
  - graph_global: 1 of 3 wrong-evidence questions involve same_company_other_period
- **directional, rule not met**: H4, global questions favour community summaries: on global, the mean paired difference (graph_global - vector) in judged answer relevancy is at least 0.1 and its 95% interval excludes 0.
  - graph_global - vector: mean +0.004 [-0.087, +0.096], n 5
- **not supported**: H5, graphs cost more: on the questions each graph arm and vector both answered, every graph arm's p95 total_ms is at least 2x vector's and its mean answer-side cost per question is higher.
  - graph_local: p95 total_ms 9,901 against 11,183 (0.89x); cost $0.0289 against $0.0304, n 47
  - graph_traversal: p95 total_ms 21,959 against 11,183 (1.96x); cost $0.0324 against $0.0304, n 47
  - graph_global: p95 total_ms 14,094 against 11,773 (1.20x); cost $0.0759 against $0.0305, n 14
- **supported**: H6, the router is useful but imperfect: on dev, the pre-registered router picks the arm matching the gold class (its predicted class is the gold class) on at least 80% of answerable and decline questions, and declines every decline question; read on the point estimate, against agent-drafted class labels.
  - router accuracy 76 of 88 (0.864)
  - decline questions declined: 6 of 6

## Router

The router rows are not arms: the router classifies each question with claude-haiku-5-5, declines investment advice itself and hands the rest to the arm its routing map names. The dev-best map is tuned on dev.

### Classifier on `dev` (`eval.route`, no arm run)

The effort sweep under `route/v1` (the most accurate effort wins, ties to the lower), then at most one revision at that effort, driven by the sweep's confusion matrices (in the route run files). The more accurate version is the one the router uses. Accuracy is against agent-drafted class labels.

| run | prompt | effort | accuracy [Wilson 95%] | declines declined | false declines | spent (USD) | commit | router uses |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `benchmarks/router/route-run1.json` | `route/v1@a8229c31` | low | 75 of 88 0.852 [0.763, 0.912] | 6 of 6 | 0 | 0.0064 | `b506476` |  |
| `benchmarks/router/route-run2.json` | `route/v1@a8229c31` | medium | 72 of 88 0.818 [0.725, 0.885] | 6 of 6 | 0 | 0.0059 | `b506476` |  |
| `benchmarks/router/route-run3.json` | `route/v1@a8229c31` | high | 71 of 88 0.807 [0.712, 0.876] | 6 of 6 | 0 | 0.0072 | `b506476` |  |
| `benchmarks/router/route-run4.json` | `route/v2@67290827` | low | 76 of 88 0.864 [0.777, 0.920] | 6 of 6 | 0 | 0.0068 | `fa43bbb` | **yes** |

### Router accuracy on `dev`

Router accuracy (against agent-drafted class labels), from `benchmarks/runs/2026-10-18-router-pre_registered-dev-c1b171d.json`: 76 of 88, 0.864 [0.777, 0.920] (Wilson 95%). Answerable and decline questions count; a decline question counts only when the router declined it.

| gold \ predicted | lookup | local | multi_hop | global | decline | accuracy |
| --- | --- | --- | --- | --- | --- | --- |
| lookup | 27 | 0 | 0 | 0 | 0 | 27 of 27 |
| local | 6 | 13 | 3 | 0 | 0 | 13 of 22 |
| multi_hop | 0 | 0 | 21 | 2 | 0 | 21 of 23 |
| global | 0 | 0 | 1 | 9 | 0 | 9 of 10 |
| decline | 0 | 0 | 0 | 0 | 6 | 6 of 6 |

Decline questions declined: 6 of 6 (missed: none). False declines (a question declined that is not a decline question): none. Failed classifications: 0.

### Unanswerable questions under the router on `dev`

Unanswerable questions are left out of accuracy: no class names them, so the question is whether the arm they reach says the filings lack the answer.

| router | unanswerable n | predicted classes | answered not_found | declined |
| --- | --- | --- | --- | --- |
| router (pre-registered) | 5 | lookup 1.00 | 5 of 5 | 0 of 5 |
| router (dev-best) | 5 | lookup 1.00 | 5 of 5 | 0 of 5 |

### Oracle router on `dev`

The oracle gives each answerable question its gold class and the pre-registered map's arm, reading that arm's own run on the same split, so it costs nothing. The gap between the oracle and the pre-registered router is misclassification, plus any difference between the router's run of an arm and that arm's own run (an arm whose model calls missed the cache reran them; see each run's commit and graph load run).

| class | oracle arm | oracle recall@10 | router (pre-registered) recall@10 | router (dev-best) recall@10 |
| --- | --- | --- | --- | --- |
| lookup | vector | 1.000 | 1.000 | 0.963 |
| local | graph_local | 0.909 | 0.886 | 0.909 |
| multi_hop | graph_traversal | 0.703 | 0.623 | 0.670 |
| global | graph_global | 0.458 | 0.492 | 0.767 |
| pooled | - | 0.826 | 0.802 | 0.842 |

### Router accuracy on `test`

Router accuracy (against agent-drafted class labels), from `benchmarks/runs/2026-10-19-router-pre_registered-test-f761581.json`: 38 of 42, 0.905 [0.779, 0.962] (Wilson 95%). Answerable and decline questions count; a decline question counts only when the router declined it.

| gold \ predicted | lookup | local | multi_hop | global | decline | accuracy |
| --- | --- | --- | --- | --- | --- | --- |
| lookup | 12 | 1 | 0 | 0 | 0 | 12 of 13 |
| local | 2 | 10 | 1 | 0 | 0 | 10 of 13 |
| multi_hop | 0 | 0 | 7 | 0 | 0 | 7 of 7 |
| global | 0 | 0 | 0 | 5 | 0 | 5 of 5 |
| decline | 0 | 0 | 0 | 0 | 4 | 4 of 4 |

Decline questions declined: 4 of 4 (missed: none). False declines (a question declined that is not a decline question): none. Failed classifications: 0.

### Unanswerable questions under the router on `test`

Unanswerable questions are left out of accuracy: no class names them, so the question is whether the arm they reach says the filings lack the answer.

| router | unanswerable n | predicted classes | answered not_found | declined |
| --- | --- | --- | --- | --- |
| router (pre-registered) | 5 | lookup 1.00 | 5 of 5 | 0 of 5 |
| router (dev-best) | 5 | lookup 1.00 | 5 of 5 | 0 of 5 |

### Oracle router on `test`

The oracle gives each answerable question its gold class and the pre-registered map's arm, reading that arm's own run on the same split, so it costs nothing. The gap between the oracle and the pre-registered router is misclassification, plus any difference between the router's run of an arm and that arm's own run (an arm whose model calls missed the cache reran them; see each run's commit and graph load run).

| class | oracle arm | oracle recall@10 | router (pre-registered) recall@10 | router (dev-best) recall@10 |
| --- | --- | --- | --- | --- |
| lookup | vector | 1.000 | 1.000 | 1.000 |
| local | graph_local | 0.500 | 0.500 | 0.500 |
| multi_hop | graph_traversal | 0.524 | 0.524 | 0.500 |
| global | graph_global | 0.333 | 0.333 | 0.800 |
| pooled | - | 0.654 | 0.654 | 0.711 |

### Dev-best routing map (tuned on dev)

Rule: per answerable class, the arm with the highest mean dev recall@10; an arm whose paired recall@10 difference against it has a 95% interval including 0 ties with it; among tied arms the lowest dev cost per question (answer side, judges excluded) wins, an exact cost tie going to the earlier arm.

Written to `benchmarks/router/dev-best-map.json` at `d2f3fa1`.

| class | arm | recall@10 | difference vs best [95%] | tied | dev cost / question (USD) | chosen |
|---|---|---|---|---|---|---|
| lookup | vector | 1.000 | best | yes | 0.0310 |  |
| lookup | graph_local | 0.963 | -0.037 [-0.111, 0.000] | yes | 0.0304 | **yes** |
| lookup | graph_traversal | 0.963 | -0.037 [-0.111, 0.000] | yes | 0.0344 |  |
| lookup | graph_global | 0.556 | -0.444 [-0.630, -0.259] | no | 0.0822 |  |
| local | vector | 0.977 | best | yes | 0.0310 |  |
| local | graph_local | 0.909 | -0.068 [-0.182, 0.000] | yes | 0.0304 | **yes** |
| local | graph_traversal | 0.818 | -0.159 [-0.318, -0.023] | no | 0.0344 |  |
| local | graph_global | 0.841 | -0.136 [-0.273, 0.000] | yes | 0.0822 |  |
| multi_hop | vector | 0.717 | best | yes | 0.0310 |  |
| multi_hop | graph_local | 0.627 | -0.091 [-0.283, 0.109] | yes | 0.0304 | **yes** |
| multi_hop | graph_traversal | 0.703 | -0.014 [-0.217, 0.181] | yes | 0.0344 |  |
| multi_hop | graph_global | 0.489 | -0.228 [-0.395, -0.069] | no | 0.0822 |  |
| global | vector | 0.800 | best | yes | 0.0310 | **yes** |
| global | graph_local | 0.425 | -0.375 [-0.575, -0.183] | no | 0.0304 |  |
| global | graph_traversal | 0.367 | -0.433 [-0.625, -0.242] | no | 0.0344 |  |
| global | graph_global | 0.458 | -0.342 [-0.500, -0.183] | no | 0.0822 |  |

graph_global's dev cost per question comes from its full run, which answered only global questions; its other classes were scored retrieval only.

## Other measured numbers

### Cypher validity

| prompt | n | first pass valid | valid after repair | non-empty | fallback | source |
|---|---|---|---|---|---|---|
| cypher/v1@44e50e3f | 93 | 88 (0.946) | 93 (1.000) | 93 (1.000) | 0 (0.000) | `benchmarks/graph/cypher-validity-run1.json` at `5a1681d` |
| cypher/v2@f166de73 | 93 | 93 (1.000) | 93 (1.000) | 93 (1.000) | 0 (0.000) | `benchmarks/graph/cypher-validity-run2.json` at `e8e8ff3` |

Agent-drafted questions, dev, all classes. `cypher/v1` is the one the `graph_traversal` arm uses: `cypher/v2` was more often valid but retrieved worse (Step 11).

### Entity resolution

| round | precision | recall | adjudicator accuracy | source |
|---|---|---|---|---|
| 1 (scoring) | 1.000 [1.000, 1.000] | 0.387 [0.199, 0.611] | 0.917 [0.850, 0.983] | `benchmarks/resolution/scoring-score1.json` at `68b35fd` |
| 2 (scoring2) | 1.000 [1.000, 1.000] | 0.783 [0.600, 0.947] | 0.900 [0.817, 0.967] | `benchmarks/resolution/scoring2-score1.json` at `99882ce` |

Pairwise, weighted by stratum, fixed-seed bootstrap 95% intervals; agent-labelled (gpt-6-luna). Rounds 1 and 2 are different draws, not a paired comparison; round 2 (group cut) is the current figure.

### Extraction

| review round | filing | judged | accuracy [Wilson 95%] | prompt | source |
|---|---|---|---|---|---|
| 2 (Step 8) | 0001045810-26-000021 | 30 | 1.000 [0.886, 1.000] | extract/v1@6a0f055a | `benchmarks/extraction/0001045810-26-000021-round2.jsonl` at `d4cf18e` |
| 3 (Step 9) | 0000002488-26-000018 | 30 | 1.000 [0.886, 1.000] | extract/v2@34a82e2b | `benchmarks/extraction/0000002488-26-000018-round1.jsonl` at `3fd49ff` |

Accuracy is the user's judgment of 30 sampled edges per round (Step 8 round 2: NVIDIA FY2026 10-K, claude-sonnet-5-5; Step 9 round 3: AMD FY2025 10-K, claude-haiku-5-5). The two rounds judge different models and prompts and are not compared.

Over the latest `extract/v2` run of each of the 24 filings (`benchmarks/extraction/*-run*.json`, commit `67662a2`): violation rate 0.000 (0 ontology violations reached the write path out of 26,915 candidate triples); rejection rate 0.012 (327 candidates rejected before it).

### Risk-factor persistence links

Precision 0.958 [0.864, 1.000], recall 0.428 [0.298, 0.572]; agent-labelled (gpt-6-luna); `benchmarks/persistence/scoring-score1.json` at `26f1299`.

### graph_global provenance

`graph_global` dev run `2026-10-16-graph_global-dev-5bb0799.json`: 163 kept citations, 0 breaks through any selected community (the gate); 0 breaks when each citation must trace through the community of the kept map point that cited it. Agent-drafted questions; `benchmarks/graph/global-provenance-run2.json` at `d2f3fa1`.

`graph_global` test run `2026-10-19-graph_global-test-f761581.json`: 82 kept citations, 0 breaks through any selected community (the gate); 0 breaks when each citation must trace through the community of the kept map point that cited it. Agent-drafted questions; `benchmarks/graph/global-provenance-run3.json` at `4579da0`.

### graph_global chunk-handoff ablation

`graph_global` chunk handoff, ablation, dev (the arm is unchanged), agent-drafted questions, dev, answerable only; `benchmarks/graph/global-handoff-run1.json` at `b8214f0`.

| class | n | as built recall@5 | as built recall@10 | handoff recall@5 | handoff recall@10 |
|---|---|---|---|---|---|
| lookup | 27 | 0.370 | 0.556 | 0.556 | 0.593 |
| local | 22 | 0.682 | 0.841 | 0.818 | 0.841 |
| multi_hop | 23 | 0.348 | 0.489 | 0.471 | 0.543 |
| global | 10 | 0.333 | 0.458 | 0.400 | 0.525 |
| pooled | 82 | 0.443 | 0.602 | 0.583 | 0.637 |

### Judge calibration

Calibration: deferred (user decision, 2026-10-19; RESEARCH-PLAN amendment). The user has not scored the drawn answers, so every judged column is uncalibrated. The tools, draw and claims are committed (`python -m eval.calibrate`).

Corruption test, not calibration: `benchmarks/calibration/corruption-run1.json` at `8ed7350+dirty`. Four known errors planted in 30 dev answers outside the calibration draw. A faithfulness catch is a lower mean score than the uncorrupted answer's; a citation-support catch is a not-supported majority on the changed sentence.

| error | copies | skipped | faithfulness caught [Wilson 95%] | faithfulness before -> after | citation support caught [Wilson 95%] | already not supported |
|---|---|---|---|---|---|---|
| changed number | 24 | 6 | 23 of 24 (0.958 [0.798, 0.993]) | 0.929 -> 0.767 | 24 of 24 (1.000 [0.862, 1.000]) | 1 |
| swapped period | 18 | 12 | 16 of 18 (0.889 [0.672, 0.969]) | 0.905 -> 0.750 | 18 of 18 (1.000 [0.824, 1.000]) | 2 |
| wrong citation | 29 | 1 | 0 of 29 (0.000 [0.000, 0.117]) | 0.920 -> 0.920 | 29 of 29 (1.000 [0.883, 1.000]) | 2 |
| added sentence | 30 | 0 | 29 of 30 (0.967 [0.833, 0.994]) | 0.922 -> 0.753 | 30 of 30 (1.000 [0.886, 1.000]) | 0 |

Faithfulness reads the answer without its citations, so a wrong citation cannot lower it: that row is a control.

## `dev` results

Tuning data: every choice (hop count, Cypher prompt, N, the dev-best map) was made on these questions.

The router rows are not arms: the router classifies each question with claude-haiku-5-5, declines investment advice itself and hands the rest to the arm its routing map names. The dev-best map is tuned on dev.

Answerable classes. Intervals are percentile bootstrap 95% over the cell's questions; a cell under 10 questions is directional.

| class | arm | n | recall@5 | recall@10 | precision@10 | wrong evidence | faithfulness (uncalibrated) | answer relevancy (uncalibrated) | citation support (uncalibrated) | p50 total ms | p95 total ms | cost / question (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| lookup | vector | 27 | 0.889 [0.741, 1.000] | 1.000 [1.000, 1.000] | 0.100 [0.100, 0.100] | 0.000 [0.000, 0.000] | 0.986 [0.967, 0.998] | 0.874 [0.830, 0.913] | 0.943 [0.887, 0.988] | not recorded | not recorded | 0.0265 [0.0260, 0.0271] |
| lookup | graph_local | 27 | 0.630 [0.444, 0.815] | 0.963 [0.889, 1.000] | 0.096 [0.089, 0.100] | 0.074 [0.000, 0.185] | 0.971 [0.928, 1.000] | 0.855 [0.805, 0.900] | 0.931 [0.856, 0.991] | not recorded | not recorded | 0.0261 [0.0254, 0.0269] |
| lookup | graph_traversal | 27 | 0.963 [0.889, 1.000] | 0.963 [0.889, 1.000] | 0.096 [0.089, 0.100] | 0.000 [0.000, 0.000] | 0.959 [0.904, 0.999] | 0.827 [0.757, 0.883] | 0.941 [0.849, 1.000] | not recorded | not recorded | 0.0270 [0.0234, 0.0312] |
| lookup | graph_global | 27 | 0.370 [0.185, 0.556] | 0.556 [0.370, 0.741] | 0.056 [0.037, 0.074] | 0.000 [0.000, 0.000] (ranks only) | retrieval only | retrieval only | retrieval only | retrieval only | retrieval only | retrieval only |
| lookup | router (pre-registered) | 27 | 0.889 [0.778, 1.000] | 1.000 [1.000, 1.000] | 0.100 [0.100, 0.100] | 0.000 [0.000, 0.000] | 0.986 [0.967, 0.998] | 0.874 [0.829, 0.912] | 0.943 [0.886, 0.988] | 17 [15, 19] (27 replayed) | 25 [21, 26] | 0.0266 [0.0260, 0.0272] |
| lookup | router (dev-best) | 27 | 0.630 [0.444, 0.815] | 0.963 [0.889, 1.000] | 0.096 [0.089, 0.100] | 0.074 [0.000, 0.185] | 0.971 [0.928, 1.000] | 0.855 [0.804, 0.899] | 0.931 [0.853, 0.991] | 86 [82, 88] (27 replayed) | 101 [92, 128] | 0.0262 [0.0254, 0.0270] |
| local | vector | 22 | 0.932 [0.864, 1.000] | 0.977 [0.932, 1.000] | 0.118 [0.105, 0.136] | 0.000 [0.000, 0.000] | 0.984 [0.966, 0.996] | 0.744 [0.703, 0.783] | 0.973 [0.951, 0.992] | not recorded | not recorded | 0.0334 [0.0319, 0.0349] |
| local | graph_local | 22 | 0.727 [0.545, 0.886] | 0.909 [0.795, 1.000] | 0.109 [0.091, 0.127] | 0.045 [0.000, 0.136] | 0.992 [0.985, 0.997] | 0.732 [0.694, 0.768] | 0.986 [0.976, 0.995] | not recorded | not recorded | 0.0318 [0.0304, 0.0331] |
| local | graph_traversal | 22 | 0.795 [0.636, 0.932] | 0.818 [0.659, 0.955] | 0.100 [0.077, 0.123] | 0.000 [0.000, 0.000] | 0.966 [0.930, 0.992] | 0.730 [0.678, 0.778] | 0.975 [0.954, 0.993] | not recorded | not recorded | 0.0389 [0.0351, 0.0422] |
| local | graph_global | 22 | 0.682 [0.500, 0.864] | 0.841 [0.682, 0.977] | 0.100 [0.077, 0.123] | 0.000 [0.000, 0.000] (ranks only) | retrieval only | retrieval only | retrieval only | retrieval only | retrieval only | retrieval only |
| local | router (pre-registered) | 22 | 0.795 [0.636, 0.932] | 0.886 [0.750, 1.000] | 0.109 [0.086, 0.132] | 0.045 [0.000, 0.136] | 0.979 [0.957, 0.996] | 0.731 [0.693, 0.769] | 0.980 [0.960, 0.995] | 110 [91, 176] (22 replayed) | 12,105 [256, 12,827] | 0.0333 [0.0312, 0.0354] |
| local | router (dev-best) | 22 | 0.727 [0.545, 0.886] | 0.909 [0.795, 1.000] | 0.109 [0.091, 0.127] | 0.045 [0.000, 0.136] | 0.992 [0.986, 0.997] | 0.732 [0.695, 0.768] | 0.986 [0.976, 0.995] | 125 [97, 170] (22 replayed) | 261 [187, 498] | 0.0319 [0.0305, 0.0332] |
| multi_hop | vector | 23 | 0.616 [0.457, 0.775] | 0.717 [0.543, 0.870] | 0.157 [0.117, 0.196] | 0.130 [0.000, 0.261] | 0.944 [0.905, 0.977] | 0.786 [0.746, 0.822] | 0.975 [0.957, 0.991] | not recorded | not recorded | 0.0322 [0.0309, 0.0337] |
| multi_hop | graph_local | 23 | 0.486 [0.326, 0.652] | 0.627 [0.489, 0.761] | 0.139 [0.109, 0.170] | 0.130 [0.000, 0.261] | 0.844 [0.744, 0.927] | 0.761 [0.721, 0.798] | 0.946 [0.877, 0.989] | not recorded | not recorded | 0.0317 [0.0304, 0.0331] |
| multi_hop | graph_traversal | 23 | 0.569 [0.431, 0.703] | 0.703 [0.551, 0.848] | 0.157 [0.122, 0.191] | 0.043 [0.000, 0.130] | 0.936 [0.858, 0.986] | 0.769 [0.727, 0.811] | 0.973 [0.944, 0.995] | not recorded | not recorded | 0.0346 [0.0300, 0.0395] |
| multi_hop | graph_global | 23 | 0.348 [0.196, 0.507] | 0.489 [0.341, 0.641] | 0.109 [0.074, 0.143] | 0.174 [0.043, 0.348] (ranks only) | retrieval only | retrieval only | retrieval only | retrieval only | retrieval only | retrieval only |
| multi_hop | router (pre-registered) | 23 | 0.536 [0.391, 0.674] | 0.623 [0.471, 0.768] | 0.139 [0.104, 0.170] | 0.043 [0.000, 0.130] | 0.919 [0.844, 0.975] | 0.735 [0.674, 0.789] | 0.979 [0.955, 0.997] | 9,517 [7,639, 10,526] (23 replayed) | 16,351 [11,773, 16,511] | 0.0383 [0.0305, 0.0480] |
| multi_hop | router (dev-best) | 23 | 0.514 [0.362, 0.667] | 0.670 [0.522, 0.804] | 0.152 [0.117, 0.187] | 0.130 [0.000, 0.261] | 0.878 [0.808, 0.939] | 0.768 [0.727, 0.807] | 0.975 [0.958, 0.990] | 98 [89, 107] (23 replayed) | 123 [110, 126] | 0.0318 [0.0304, 0.0334] |
| global | vector | 10 | 0.592 [0.383, 0.800] | 0.800 [0.633, 0.950] | 0.260 [0.210, 0.310] | 0.200 [0.000, 0.500] | 0.787 [0.686, 0.890] | 0.781 [0.747, 0.817] | 0.974 [0.928, 1.000] | not recorded | not recorded | 0.0354 [0.0330, 0.0378] |
| global | graph_local | 10 | 0.283 [0.108, 0.492] | 0.425 [0.208, 0.650] | 0.140 [0.070, 0.210] | 0.300 [0.000, 0.600] | 0.805 [0.628, 0.932] | 0.714 [0.639, 0.777] | 0.970 [0.927, 1.000] | not recorded | not recorded | 0.0356 [0.0328, 0.0385] |
| global | graph_traversal | 10 | 0.208 [0.083, 0.342] | 0.367 [0.175, 0.575] | 0.120 [0.060, 0.180] | 0.300 [0.000, 0.600] | 0.839 [0.705, 0.938] | 0.560 [0.400, 0.693] | 0.931 [0.853, 1.000] | not recorded | not recorded | 0.0439 [0.0381, 0.0490] |
| global | graph_global | 10 | 0.333 [0.200, 0.475] | 0.458 [0.300, 0.617] | 0.150 [0.100, 0.200] | 0.200 [0.000, 0.500] | 0.818 [0.668, 0.936] | 0.694 [0.602, 0.777] | 0.931 [0.885, 0.970] | not recorded | not recorded | 0.0822 [0.0682, 0.0948] |
| global | router (pre-registered) | 10 | 0.367 [0.250, 0.483] | 0.492 [0.367, 0.633] | 0.160 [0.120, 0.200] | 0.200 [0.000, 0.500] | 0.818 [0.674, 0.937] | 0.667 [0.566, 0.764] | 0.936 [0.889, 0.975] | 45 [36, 271] (10 replayed) | 11,921 [47, 21,270] | 0.0788 [0.0662, 0.0906] |
| global | router (dev-best) | 10 | 0.592 [0.367, 0.800] | 0.767 [0.550, 0.950] | 0.250 [0.180, 0.310] | 0.200 [0.000, 0.500] | 0.779 [0.678, 0.876] | 0.756 [0.680, 0.816] | 0.974 [0.928, 1.000] | 27 [23, 132] (10 replayed) | 1,392 [34, 2,342] | 0.0351 [0.0330, 0.0375] |

Decline and unanswerable questions: whether the answer declined or said the filings lack the evidence.

| class | arm | n | correct (uncalibrated) | p50 total ms | p95 total ms | cost / question (USD) |
| --- | --- | --- | --- | --- | --- | --- |
| decline | vector | 6 (directional) | 1.000 [1.000, 1.000] | not recorded | not recorded | 0.0255 [0.0241, 0.0269] |
| decline | graph_local | 6 (directional) | 1.000 [1.000, 1.000] | not recorded | not recorded | 0.0249 [0.0236, 0.0261] |
| decline | graph_traversal | 6 (directional) | 1.000 [1.000, 1.000] | not recorded | not recorded | 0.0446 [0.0344, 0.0528] |
| decline | graph_global | 6 (directional) | 1.000 [1.000, 1.000] | not recorded | not recorded | 0.0854 [0.0780, 0.0904] |
| decline | router (pre-registered) | 6 (directional) | 1.000 [1.000, 1.000] | 0 [0, 3] (6 replayed) | 4 [0, 6] | 0.0002 [0.0001, 0.0002] |
| decline | router (dev-best) | 6 (directional) | 1.000 [1.000, 1.000] | 0 [0, 3] (6 replayed) | 5 [0, 6] | 0.0002 [0.0001, 0.0002] |
| unanswerable | vector | 5 (directional) | 1.000 [1.000, 1.000] | not recorded | not recorded | 0.0283 [0.0273, 0.0294] |
| unanswerable | graph_local | 5 (directional) | 1.000 [1.000, 1.000] | not recorded | not recorded | 0.0269 [0.0259, 0.0279] |
| unanswerable | graph_traversal | 5 (directional) | 1.000 [1.000, 1.000] | not recorded | not recorded | 0.0323 [0.0238, 0.0382] |
| unanswerable | graph_global | 5 (directional) | 1.000 [1.000, 1.000] | not recorded | not recorded | 0.0681 [0.0613, 0.0753] |
| unanswerable | router (pre-registered) | 5 (directional) | 1.000 [1.000, 1.000] | 31 [25, 1,996] (5 replayed) | 1,603 [30, 1,996] | 0.0284 [0.0273, 0.0294] |
| unanswerable | router (dev-best) | 5 (directional) | 1.000 [1.000, 1.000] | 345 [212, 6,261] (5 replayed) | 5,090 [326, 6,261] | 0.0269 [0.0259, 0.0279] |

### `dev` paired differences against `vector`

On the questions both runs hold, as `eval.compare` computes them; a difference counts only when its interval excludes 0. graph_global's judged differences compare whole pipelines (its reduce prompt is not `answer/v1`).

| arm | class | metric | n | difference (arm - vector) [95%] | counts | note |
| --- | --- | --- | --- | --- | --- | --- |
| graph_local | lookup | recall@5 | 27 | -0.259 [-0.481, -0.037] | yes | - |
| graph_local | lookup | recall@10 | 27 | -0.037 [-0.111, 0.000] | no | - |
| graph_local | lookup | precision@10 | 27 | -0.004 [-0.011, 0.000] | no | - |
| graph_local | lookup | wrong evidence | 27 | 0.074 [0.000, 0.185] | no | - |
| graph_local | lookup | faithfulness | 27 | -0.015 [-0.045, 0.005] | no | uncalibrated |
| graph_local | lookup | answer relevancy | 27 | -0.020 [-0.042, -0.001] | yes | uncalibrated |
| graph_local | lookup | citation support | 27 | -0.012 [-0.074, 0.049] | no | uncalibrated |
| graph_local | local | recall@5 | 22 | -0.205 [-0.386, -0.045] | yes | - |
| graph_local | local | recall@10 | 22 | -0.068 [-0.182, 0.000] | no | - |
| graph_local | local | precision@10 | 22 | -0.009 [-0.023, 0.000] | no | - |
| graph_local | local | wrong evidence | 22 | 0.045 [0.000, 0.136] | no | - |
| graph_local | local | faithfulness | 22 | 0.008 [-0.005, 0.025] | no | uncalibrated |
| graph_local | local | answer relevancy | 22 | -0.012 [-0.042, 0.018] | no | uncalibrated |
| graph_local | local | citation support | 22 | 0.014 [-0.002, 0.032] | no | uncalibrated |
| graph_local | multi_hop | recall@5 | 23 | -0.130 [-0.341, 0.072] | no | - |
| graph_local | multi_hop | recall@10 | 23 | -0.091 [-0.283, 0.109] | no | - |
| graph_local | multi_hop | precision@10 | 23 | -0.017 [-0.061, 0.030] | no | - |
| graph_local | multi_hop | wrong evidence | 23 | 0.000 [-0.174, 0.174] | no | - |
| graph_local | multi_hop | faithfulness | 23 | -0.100 [-0.184, -0.028] | yes | uncalibrated |
| graph_local | multi_hop | answer relevancy | 23 | -0.025 [-0.059, 0.009] | no | uncalibrated |
| graph_local | multi_hop | citation support | 23 | -0.028 [-0.104, 0.021] | no | uncalibrated |
| graph_local | global | recall@5 | 10 | -0.308 [-0.492, -0.150] | yes | - |
| graph_local | global | recall@10 | 10 | -0.375 [-0.575, -0.183] | yes | - |
| graph_local | global | precision@10 | 10 | -0.120 [-0.180, -0.060] | yes | - |
| graph_local | global | wrong evidence | 10 | 0.100 [0.000, 0.300] | no | - |
| graph_local | global | faithfulness | 10 | 0.018 [-0.120, 0.120] | no | uncalibrated |
| graph_local | global | answer relevancy | 10 | -0.067 [-0.127, -0.011] | yes | uncalibrated |
| graph_local | global | citation support | 10 | -0.004 [-0.057, 0.058] | no | uncalibrated |
| graph_local | pooled | recall@5 | 82 | -0.214 [-0.320, -0.107] | yes | - |
| graph_local | pooled | recall@10 | 82 | -0.102 [-0.177, -0.030] | yes | - |
| graph_local | pooled | precision@10 | 82 | -0.023 [-0.041, -0.005] | yes | - |
| graph_local | pooled | wrong evidence | 82 | 0.049 [-0.012, 0.122] | no | - |
| graph_local | pooled | faithfulness | 82 | -0.029 [-0.061, -0.001] | yes | uncalibrated |
| graph_local | pooled | answer relevancy | 82 | -0.025 [-0.041, -0.009] | yes | uncalibrated |
| graph_local | pooled | citation support | 82 | -0.009 [-0.039, 0.019] | no | uncalibrated |
| graph_local | decline | decline correct | 6 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| graph_local | unanswerable | not found correct | 5 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| graph_traversal | lookup | recall@5 | 27 | 0.074 [-0.074, 0.222] | no | - |
| graph_traversal | lookup | recall@10 | 27 | -0.037 [-0.111, 0.000] | no | - |
| graph_traversal | lookup | precision@10 | 27 | -0.004 [-0.011, 0.000] | no | - |
| graph_traversal | lookup | wrong evidence | 27 | 0.000 [0.000, 0.000] | no | - |
| graph_traversal | lookup | faithfulness | 27 | -0.027 [-0.087, 0.019] | no | uncalibrated |
| graph_traversal | lookup | answer relevancy | 27 | -0.048 [-0.117, -0.005] | yes | uncalibrated |
| graph_traversal | lookup | citation support | 27 | -0.002 [-0.095, 0.071] | no | uncalibrated |
| graph_traversal | local | recall@5 | 22 | -0.136 [-0.318, 0.000] | no | - |
| graph_traversal | local | recall@10 | 22 | -0.159 [-0.318, -0.023] | yes | - |
| graph_traversal | local | precision@10 | 22 | -0.018 [-0.036, -0.005] | yes | - |
| graph_traversal | local | wrong evidence | 22 | 0.000 [0.000, 0.000] | no | - |
| graph_traversal | local | faithfulness | 22 | -0.018 [-0.054, 0.004] | no | uncalibrated |
| graph_traversal | local | answer relevancy | 22 | -0.014 [-0.054, 0.023] | no | uncalibrated |
| graph_traversal | local | citation support | 22 | 0.003 [-0.022, 0.026] | no | uncalibrated |
| graph_traversal | multi_hop | recall@5 | 23 | -0.047 [-0.239, 0.138] | no | - |
| graph_traversal | multi_hop | recall@10 | 23 | -0.014 [-0.217, 0.181] | no | - |
| graph_traversal | multi_hop | precision@10 | 23 | 0.000 [-0.043, 0.043] | no | - |
| graph_traversal | multi_hop | wrong evidence | 23 | -0.087 [-0.217, 0.000] | no | - |
| graph_traversal | multi_hop | faithfulness | 23 | -0.007 [-0.087, 0.059] | no | uncalibrated |
| graph_traversal | multi_hop | answer relevancy | 23 | -0.016 [-0.050, 0.021] | no | uncalibrated |
| graph_traversal | multi_hop | citation support | 23 | -0.002 [-0.038, 0.030] | no | uncalibrated |
| graph_traversal | global | recall@5 | 10 | -0.383 [-0.583, -0.200] | yes | - |
| graph_traversal | global | recall@10 | 10 | -0.433 [-0.625, -0.242] | yes | - |
| graph_traversal | global | precision@10 | 10 | -0.140 [-0.200, -0.080] | yes | - |
| graph_traversal | global | wrong evidence | 10 | 0.100 [-0.200, 0.400] | no | - |
| graph_traversal | global | faithfulness | 10 | 0.053 [-0.105, 0.213] | no | uncalibrated |
| graph_traversal | global | answer relevancy | 10 | -0.221 [-0.371, -0.095] | yes | uncalibrated |
| graph_traversal | global | citation support | 10 | -0.043 [-0.136, 0.046] | no | uncalibrated |
| graph_traversal | pooled | recall@5 | 82 | -0.072 [-0.165, 0.016] | no | - |
| graph_traversal | pooled | recall@10 | 82 | -0.112 [-0.195, -0.032] | yes | - |
| graph_traversal | pooled | precision@10 | 82 | -0.023 [-0.041, -0.005] | yes | - |
| graph_traversal | pooled | wrong evidence | 82 | -0.012 [-0.061, 0.037] | no | - |
| graph_traversal | pooled | faithfulness | 82 | -0.009 [-0.045, 0.024] | no | uncalibrated |
| graph_traversal | pooled | answer relevancy | 82 | -0.051 [-0.087, -0.021] | yes | uncalibrated |
| graph_traversal | pooled | citation support | 82 | -0.006 [-0.040, 0.024] | no | uncalibrated |
| graph_traversal | decline | decline correct | 6 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| graph_traversal | unanswerable | not found correct | 5 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| graph_global | lookup | recall@5 | 27 | -0.519 [-0.704, -0.333] | yes | - |
| graph_global | lookup | recall@10 | 27 | -0.444 [-0.630, -0.259] | yes | - |
| graph_global | lookup | precision@10 | 27 | -0.044 [-0.063, -0.026] | yes | - |
| graph_global | lookup | wrong evidence | 27 | 0.000 [0.000, 0.000] | no | - |
| graph_global | local | recall@5 | 22 | -0.250 [-0.432, -0.091] | yes | - |
| graph_global | local | recall@10 | 22 | -0.136 [-0.273, 0.000] | no | - |
| graph_global | local | precision@10 | 22 | -0.018 [-0.041, 0.000] | no | - |
| graph_global | local | wrong evidence | 22 | 0.000 [0.000, 0.000] | no | - |
| graph_global | multi_hop | recall@5 | 23 | -0.268 [-0.457, -0.087] | yes | - |
| graph_global | multi_hop | recall@10 | 23 | -0.228 [-0.395, -0.069] | yes | - |
| graph_global | multi_hop | precision@10 | 23 | -0.048 [-0.083, -0.013] | yes | - |
| graph_global | multi_hop | wrong evidence | 23 | 0.043 [-0.087, 0.174] | no | - |
| graph_global | global | recall@5 | 10 | -0.258 [-0.508, -0.025] | yes | - |
| graph_global | global | recall@10 | 10 | -0.342 [-0.500, -0.183] | yes | - |
| graph_global | global | precision@10 | 10 | -0.110 [-0.160, -0.060] | yes | - |
| graph_global | global | wrong evidence | 10 | 0.000 [0.000, 0.000] | no | - |
| graph_global | global | faithfulness | 10 | 0.031 [-0.115, 0.170] | no | uncalibrated |
| graph_global | global | answer relevancy | 10 | -0.088 [-0.160, -0.027] | yes | uncalibrated |
| graph_global | global | citation support | 10 | -0.043 [-0.085, -0.009] | yes | uncalibrated |
| graph_global | pooled | recall@5 | 82 | -0.345 [-0.446, -0.244] | yes | - |
| graph_global | pooled | recall@10 | 82 | -0.289 [-0.382, -0.199] | yes | - |
| graph_global | pooled | precision@10 | 82 | -0.046 [-0.062, -0.030] | yes | - |
| graph_global | pooled | wrong evidence | 82 | 0.012 [-0.024, 0.049] | no | - |
| graph_global | decline | decline correct | 6 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| graph_global | unanswerable | not found correct | 5 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| router (pre-registered) | lookup | recall@5 | 27 | 0.000 [0.000, 0.000] | no | - |
| router (pre-registered) | lookup | recall@10 | 27 | 0.000 [0.000, 0.000] | no | - |
| router (pre-registered) | lookup | precision@10 | 27 | 0.000 [0.000, 0.000] | no | - |
| router (pre-registered) | lookup | wrong evidence | 27 | 0.000 [0.000, 0.000] | no | - |
| router (pre-registered) | lookup | faithfulness | 27 | 0.000 [0.000, 0.000] | no | uncalibrated |
| router (pre-registered) | lookup | answer relevancy | 27 | 0.000 [0.000, 0.000] | no | uncalibrated |
| router (pre-registered) | lookup | citation support | 27 | 0.000 [0.000, 0.000] | no | uncalibrated |
| router (pre-registered) | local | recall@5 | 22 | -0.136 [-0.273, 0.000] | no | - |
| router (pre-registered) | local | recall@10 | 22 | -0.091 [-0.227, 0.000] | no | - |
| router (pre-registered) | local | precision@10 | 22 | -0.009 [-0.023, 0.000] | no | - |
| router (pre-registered) | local | wrong evidence | 22 | 0.045 [0.000, 0.136] | no | - |
| router (pre-registered) | local | faithfulness | 22 | -0.006 [-0.018, 0.004] | no | uncalibrated |
| router (pre-registered) | local | answer relevancy | 22 | -0.013 [-0.036, 0.009] | no | uncalibrated |
| router (pre-registered) | local | citation support | 22 | 0.007 [-0.005, 0.022] | no | uncalibrated |
| router (pre-registered) | multi_hop | recall@5 | 23 | -0.080 [-0.297, 0.130] | no | - |
| router (pre-registered) | multi_hop | recall@10 | 23 | -0.094 [-0.297, 0.116] | no | - |
| router (pre-registered) | multi_hop | precision@10 | 23 | -0.017 [-0.061, 0.026] | no | - |
| router (pre-registered) | multi_hop | wrong evidence | 23 | -0.087 [-0.217, 0.000] | no | - |
| router (pre-registered) | multi_hop | faithfulness | 23 | -0.025 [-0.086, 0.033] | no | uncalibrated |
| router (pre-registered) | multi_hop | answer relevancy | 23 | -0.051 [-0.113, 0.004] | no | uncalibrated |
| router (pre-registered) | multi_hop | citation support | 23 | 0.004 [-0.027, 0.033] | no | uncalibrated |
| router (pre-registered) | global | recall@5 | 10 | -0.225 [-0.492, 0.033] | no | - |
| router (pre-registered) | global | recall@10 | 10 | -0.308 [-0.483, -0.133] | yes | - |
| router (pre-registered) | global | precision@10 | 10 | -0.100 [-0.150, -0.050] | yes | - |
| router (pre-registered) | global | wrong evidence | 10 | 0.000 [0.000, 0.000] | no | - |
| router (pre-registered) | global | faithfulness | 10 | 0.031 [-0.115, 0.170] | no | uncalibrated |
| router (pre-registered) | global | answer relevancy | 10 | -0.115 [-0.191, -0.046] | yes | uncalibrated |
| router (pre-registered) | global | citation support | 10 | -0.038 [-0.080, -0.006] | yes | uncalibrated |
| router (pre-registered) | pooled | recall@5 | 82 | -0.086 [-0.168, -0.011] | yes | - |
| router (pre-registered) | pooled | recall@10 | 82 | -0.088 [-0.162, -0.018] | yes | - |
| router (pre-registered) | pooled | precision@10 | 82 | -0.020 [-0.035, -0.004] | yes | - |
| router (pre-registered) | pooled | wrong evidence | 82 | -0.012 [-0.049, 0.024] | no | - |
| router (pre-registered) | pooled | faithfulness | 82 | -0.005 [-0.029, 0.019] | no | uncalibrated |
| router (pre-registered) | pooled | answer relevancy | 82 | -0.032 [-0.054, -0.011] | yes | uncalibrated |
| router (pre-registered) | pooled | citation support | 82 | -0.001 [-0.013, 0.009] | no | uncalibrated |
| router (pre-registered) | decline | decline correct | 6 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| router (pre-registered) | unanswerable | not found correct | 5 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| router (dev-best) | lookup | recall@5 | 27 | -0.259 [-0.481, -0.037] | yes | - |
| router (dev-best) | lookup | recall@10 | 27 | -0.037 [-0.111, 0.000] | no | - |
| router (dev-best) | lookup | precision@10 | 27 | -0.004 [-0.011, 0.000] | no | - |
| router (dev-best) | lookup | wrong evidence | 27 | 0.074 [0.000, 0.185] | no | - |
| router (dev-best) | lookup | faithfulness | 27 | -0.015 [-0.045, 0.005] | no | uncalibrated |
| router (dev-best) | lookup | answer relevancy | 27 | -0.020 [-0.042, -0.001] | yes | uncalibrated |
| router (dev-best) | lookup | citation support | 27 | -0.012 [-0.074, 0.049] | no | uncalibrated |
| router (dev-best) | local | recall@5 | 22 | -0.205 [-0.386, -0.045] | yes | - |
| router (dev-best) | local | recall@10 | 22 | -0.068 [-0.182, 0.000] | no | - |
| router (dev-best) | local | precision@10 | 22 | -0.009 [-0.023, 0.000] | no | - |
| router (dev-best) | local | wrong evidence | 22 | 0.045 [0.000, 0.136] | no | - |
| router (dev-best) | local | faithfulness | 22 | 0.008 [-0.005, 0.025] | no | uncalibrated |
| router (dev-best) | local | answer relevancy | 22 | -0.012 [-0.042, 0.018] | no | uncalibrated |
| router (dev-best) | local | citation support | 22 | 0.014 [-0.002, 0.032] | no | uncalibrated |
| router (dev-best) | multi_hop | recall@5 | 23 | -0.101 [-0.312, 0.094] | no | - |
| router (dev-best) | multi_hop | recall@10 | 23 | -0.047 [-0.232, 0.141] | no | - |
| router (dev-best) | multi_hop | precision@10 | 23 | -0.004 [-0.048, 0.039] | no | - |
| router (dev-best) | multi_hop | wrong evidence | 23 | 0.000 [-0.174, 0.174] | no | - |
| router (dev-best) | multi_hop | faithfulness | 23 | -0.066 [-0.127, -0.013] | yes | uncalibrated |
| router (dev-best) | multi_hop | answer relevancy | 23 | -0.017 [-0.051, 0.016] | no | uncalibrated |
| router (dev-best) | multi_hop | citation support | 23 | 0.000 [-0.024, 0.025] | no | uncalibrated |
| router (dev-best) | global | recall@5 | 10 | 0.000 [0.000, 0.000] | no | - |
| router (dev-best) | global | recall@10 | 10 | -0.033 [-0.100, 0.000] | no | - |
| router (dev-best) | global | precision@10 | 10 | -0.010 [-0.030, 0.000] | no | - |
| router (dev-best) | global | wrong evidence | 10 | 0.000 [0.000, 0.000] | no | - |
| router (dev-best) | global | faithfulness | 10 | -0.008 [-0.023, 0.000] | no | uncalibrated |
| router (dev-best) | global | answer relevancy | 10 | -0.025 [-0.075, 0.000] | no | uncalibrated |
| router (dev-best) | global | citation support | 10 | 0.000 [0.000, 0.000] | no | uncalibrated |
| router (dev-best) | pooled | recall@5 | 82 | -0.169 [-0.272, -0.065] | yes | - |
| router (dev-best) | pooled | recall@10 | 82 | -0.048 [-0.112, 0.014] | no | - |
| router (dev-best) | pooled | precision@10 | 82 | -0.006 [-0.018, 0.007] | no | - |
| router (dev-best) | pooled | wrong evidence | 82 | 0.037 [-0.024, 0.098] | no | - |
| router (dev-best) | pooled | faithfulness | 82 | -0.022 [-0.044, -0.004] | yes | uncalibrated |
| router (dev-best) | pooled | answer relevancy | 82 | -0.018 [-0.033, -0.003] | yes | uncalibrated |
| router (dev-best) | pooled | citation support | 82 | -0.000 [-0.023, 0.022] | no | uncalibrated |
| router (dev-best) | decline | decline correct | 6 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
| router (dev-best) | unanswerable | not found correct | 5 | 0.000 [0.000, 0.000] | no | directional, uncalibrated |
