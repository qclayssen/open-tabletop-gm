# Strixhaven campus

Extracted by `scripts/campus_extract.py` from `display/static/reference/strixhaven_map_table.html`. The page is the source; this file is the readable copy.

18 places · 5 college districts · 7 walked paths · 5 battle maps

## Colleges

| Key | College |
|---|---|
| `lore` | Lorehold |
| `pris` | Prismari |
| `quan` | Quandrix |
| `silv` | Silverquill |
| `with` | Witherbloom |
| `cent` | Central |

## Districts

Each district is one SVG path on the campus map, drawn at 16% opacity in its college colour.

| College | Path | Label at |
|---|---|---|
| Lorehold | `M60,60 L420,60 L400,320 L260,380 L60,340 Z` | (120, 95) |
| Quandrix | `M580,60 L960,60 L960,380 L760,400 L640,300 Z` | (820, 95) |
| Silverquill | `M560,200 L640,300 L760,400 L700,440 L560,420 L540,300 Z` | (575, 215) |
| Prismari | `M60,360 L260,400 L320,560 L260,620 L60,600 Z` | (80, 395) |
| Witherbloom | `M60,610 L270,630 L330,580 L440,600 L460,690 L60,690 Z` | (360, 680) |

## Places

Pins are in campus SVG coordinates (a 1000×700 viewBox, y down).

### Central

- **Central Quad** (`quad`) — pin at (500, 470)  
  Lawns, lanterns and every argument on campus. Lanternfall floats a lantern here for each person lost in the invasion.
- **Firejolt Café** (`firejolt`) — pin at (610, 500)  
  Best pastries, worst gossip. Tutoring tables by the hearth; the roofs next door are easy to climb.
- **Mage Tower Stadium** (`stadium`) — pin at (500, 610)  
  Home of Mage Tower. Two towers, two mascots, a lot of shouting.
- **Omenpath Gate and Path Ward** (`omen`) — pin at (900, 520)  
  The new gate to other worlds, and the office that checks everyone's papers.
- **Skyferry Dock and Market** (`skydock`) — pin at (820, 630)  
  Where students arrive and leave. Secondhand bookstalls line the pier.
- **The Biblioplex** (`biblioplex`) — pin at (500, 350)  
  The great library, bigger inside than out. Still being rebuilt after the invasion; memorials to the fallen line its halls. The Mystical Archive is sealed. Kairos grew up in these stacks.

### Quandrix

- **Fractal Conservatory** (`conserv`) — pin at (860, 320)  
  A greenhouse where plants grow in spirals and rooms are larger inside than out.
- **Quandrix Lecture Halls** (`quanhall`) — pin at (760, 230)  
  Fractal architecture; staircases that disagree with you. Esteemed Professor Marrow teaches probability here.
- **Quandrix Spire** (`spire`) — pin at (700, 130)  
  The tallest tower in the college. Students are not invited up.

### Lorehold

- **Hall of Echoes** (`echoes`) — pin at (170, 300)  
  Lorehold's chamber for summoning the testimony of the dead.
- **Lorehold Ledger Vaults** (`ledgers`) — pin at (320, 120)  
  Travel logs, expedition records and sealed orders, going back centuries.
- **The Owl Roost** (`roost`) — pin at (250, 200)  
  Magister Vael's tower above the Lorehold archive. Tea, owls, and questions that don't get answered.

### Prismari

- **Glass Runway** (`runway`) — pin at (230, 560)  
  Prismari's stage for performance and the annual Fashion Night.
- **Prismari Workshop Vaults** (`workshops`) — pin at (130, 470)  
  Kilns, forges and elemental constructs, some finished.

### Silverquill

- **Grand Ballroom** (`ballroom`) — pin at (650, 380)  
  Silverquill's pride. Site of the annual Masquerade.
- **Silverquill Debating Halls** (`debate`) — pin at (600, 250)  
  Where words are weapons and ink is ammunition.

### Witherbloom

- **Detention Bog** (`bog`) — pin at (250, 660)  
  Detention happens here: stinging reeds, lantern light, and things that watch from the water.
- **Witherbloom Greenhouses** (`greenhouse`) — pin at (360, 640)  
  Alchemy, medicine, and the frog pond where first-years race.

## Paths

The dashed walkways. Each is a straight run between two pins.

- (500, 350) → (500, 610)
- (500, 350) → (250, 200)
- (500, 350) → (760, 230)
- (500, 470) → (230, 560)
- (500, 470) → (650, 380)
- (500, 610) → (820, 630) → (900, 520)
- (500, 470) → (360, 640) → (250, 660)

## River

`M0,520 C200,500 330,540 470,560 C620,580 760,560 1000,590`

A cubic curve, kept verbatim rather than sampled: the page draws it at 70% opacity across the lower third of the campus.

## Battle maps

The page's five maps, and the engine file each was ported to. Squares are 5 ft; `A1` is x=0, y=0.

| Page key | Engine map | Name | Size | Features | Tokens |
|---|---|---|---|---|---|
| `tower` | `mage-tower` | Mage Tower Stadium | 30×12 | 9 | 12 |
| `cafe` | `firejolt-rooftops` | Firejolt Café and Rooftops | 24×16 | 17 | 6 |
| `bog` | `detention-bog` | Detention Bog | 24×18 | 11 | 6 |
| `pond` | `frog-pond` | Frog Pond | 20×14 | 9 | 5 |
| `grid` | `blank` | Blank Grid | 24×16 | 0 | 3 |

### Mage Tower Stadium — `mage-tower.json`

30×12 squares (150×60 ft). Five zones, left to right: Quandrix Tower, Quandrix Half, Midfield, Opponent Half, Opponent Tower. Each zone is 6 squares wide. Carry the mascot into your own tower zone to score. Spells are allowed; harming a player is a foul.

Zone lines at x = 6, 12, 18, 24.

| Type | x | y | w | h | Label |
|---|---|---|---|---|---|
| Feature / cover | 0 | 0 | 6 | 12 | Quandrix Tower |
| Floor | 6 | 0 | 6 | 12 | Quandrix Half |
| Floor | 12 | 0 | 6 | 12 | Midfield |
| Floor | 18 | 0 | 6 | 12 | Opponent Half |
| Feature / cover | 24 | 0 | 6 | 12 | Opponent Tower |
| Wall / blocking | 2 | 5 | 2 | 2 |  |
| Wall / blocking | 26 | 5 | 2 | 2 |  |
| Difficult terrain | 13 | 2 | 4 | 2 | Snarl eddy |
| Difficult terrain | 13 | 8 | 4 | 2 |  |

| Token | Name | Side | Square |
|---|---|---|---|
| `K` | Kairos | Ally (teal) | `I6` |
| `M` | Mabli | Lorehold | `H9` |
| `J` | Juno | Witherbloom | `J4` |
| `Q1` | Quandrix guard | Ally (teal) | `E4` |
| `Q2` | Quandrix guard | Ally (teal) | `E9` |
| `m` | Quandrix mascot | Object | `B7` |
| `T` | Theodric | Silverquill | `V7` |
| `S` | Saffi | Prismari | `W4` |
| `R` | Rennick | Witherbloom | `W10` |
| `O1` | Opponent guard | Silverquill | `Z4` |
| `O2` | Opponent guard | Silverquill | `Z9` |
| `o` | Opponent mascot | Object | `]7` |

### Firejolt Café and Rooftops — `firejolt-rooftops.json`

24×16 squares (120×80 ft). Ground floor café (left) and the rooftops next door (right). Gaps between roofs are 2 to 3 squares: a jump or an Acrobatics check. Chimneys give half cover.

| Type | x | y | w | h | Label |
|---|---|---|---|---|---|
| Wall / blocking | 0 | 0 | 11 | 1 |  |
| Wall / blocking | 0 | 15 | 11 | 1 |  |
| Wall / blocking | 0 | 0 | 1 | 16 |  |
| Wall / blocking | 10 | 0 | 1 | 6 |  |
| Wall / blocking | 10 | 9 | 1 | 7 |  |
| Feature / cover | 2 | 2 | 3 | 2 | Hearth |
| Feature / cover | 6 | 3 | 2 | 2 | Table |
| Feature / cover | 6 | 9 | 2 | 2 | Table |
| Feature / cover | 2 | 10 | 3 | 2 | Counter |
| Drop / off-map | 11 | 0 | 13 | 16 |  |
| Floor | 12 | 1 | 5 | 6 | Roof A |
| Floor | 19 | 1 | 5 | 5 | Roof B |
| Floor | 12 | 9 | 4 | 6 | Roof C |
| Floor | 18 | 8 | 6 | 7 | Roof D |
| Wall / blocking | 14 | 3 | 1 | 1 |  |
| Wall / blocking | 21 | 11 | 1 | 1 |  |
| Wall / blocking | 13 | 11 | 1 | 1 |  |

| Token | Name | Side | Square |
|---|---|---|---|
| `K` | Kairos | Ally (teal) | `F8` |
| `J` | Juno | Witherbloom | `D8` |
| `M` | Mabli | Lorehold | `H8` |
| `H` | Masked figure | Enemy | `N4` |
| `c1` | Cultist | Enemy | `U3` |
| `c2` | Cultist | Enemy | `T11` |

### Detention Bog — `detention-bog.json`

24×18 squares (120×90 ft). Open water is swimming (half speed). Reeds are difficult terrain and give light cover. Lanterns shed bright light 3 squares around them.

| Type | x | y | w | h | Label |
|---|---|---|---|---|---|
| Water | 0 | 0 | 24 | 18 |  |
| Floor | 1 | 1 | 6 | 5 | Boardwalk |
| Difficult terrain | 7 | 2 | 6 | 4 | Reeds |
| Floor | 3 | 6 | 3 | 10 |  |
| Difficult terrain | 8 | 8 | 7 | 5 | Reeds |
| Floor | 15 | 5 | 6 | 4 | Island |
| Difficult terrain | 16 | 12 | 6 | 5 | Reeds |
| Feature / cover | 17 | 6 | 2 | 2 | Old tree |
| Floor | 1 | 14 | 6 | 3 | Reed-cutting dock |
| Feature / cover | 4 | 15 | 1 | 1 | Lantern |
| Feature / cover | 2 | 2 | 1 | 1 | Lantern |

| Token | Name | Side | Square |
|---|---|---|---|
| `K` | Kairos | Ally (teal) | `D16` |
| `J` | Juno | Witherbloom | `C16` |
| `M` | Mabli | Lorehold | `E15` |
| `b1` | Bog shrub | Enemy | `J10` |
| `b2` | Bog shrub | Enemy | `L11` |
| `s` | Stirges | Enemy | `Q7` |

### Frog Pond — `frog-pond.json`

20×14 squares (100×70 ft). The Witherbloom race course. Lily pads are difficult terrain; the finish line is on the right bank.

| Type | x | y | w | h | Label |
|---|---|---|---|---|---|
| Floor | 0 | 0 | 3 | 14 | Start bank |
| Water | 3 | 0 | 14 | 14 |  |
| Difficult terrain | 5 | 3 | 2 | 2 |  |
| Difficult terrain | 8 | 7 | 2 | 2 |  |
| Difficult terrain | 11 | 2 | 2 | 2 |  |
| Difficult terrain | 13 | 9 | 2 | 2 |  |
| Difficult terrain | 7 | 11 | 2 | 2 |  |
| Floor | 17 | 0 | 3 | 14 | Finish bank |
| Feature / cover | 17 | 0 | 1 | 14 |  |

| Token | Name | Side | Square |
|---|---|---|---|
| `K` | Kairos | Ally (teal) | `B7` |
| `J` | Juno | Witherbloom | `B9` |
| `T` | Theodric | Silverquill | `B5` |
| `f1` | Giant frog | Enemy | `J5` |
| `f2` | Giant frog | Enemy | `M11` |

### Blank Grid — `blank.json`

24×16 squares (120×80 ft). An empty 24 by 16 grid (120 by 80 feet) for any scene the other maps don't cover.

| Token | Name | Side | Square |
|---|---|---|---|
| `K` | Kairos | Ally (teal) | `D9` |
| `J` | Juno | Witherbloom | `C10` |
| `M` | Mabli | Lorehold | `E10` |
