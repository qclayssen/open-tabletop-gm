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
| `tower` | `mage-tower` | Mage Tower Stadium | 32×41 | 106 | 12 |
| `cafe` | `firejolt-rooftops` | Firejolt Café and Rooftops | 24×16 | 17 | 6 |
| `bog` | `detention-bog` | Detention Bog | 24×18 | 11 | 6 |
| `pond` | `frog-pond` | Frog Pond | 20×14 | 9 | 5 |
| `grid` | `blank` | Blank Grid | 24×16 | 0 | 3 |

### Mage Tower Stadium — `mage-tower.json`

32×41 squares (160×205 ft). The Mage Tower pitch, 160 by 205 feet. The inner octagon is in bounds, divided by the dashed line into the four sections of the game: 1 thick mud, which is difficult terrain and catches fire, 2 dry dirt, 3 a garden, 4 the pond. The planking is raised decking and gives half cover. The water ring around the octagon is the moat, and a creature in the moat is out of bounds. The terrace, the lawns and the stands beyond it are off the pitch but walkable; paint them out if you want the match contained. Towers A and B hold the mascots; the planked deck in front of each is where a creature wearing a Mage Tower Ring spends an action to restore temporary hit points. Carry an enemy mascot into your own tower to score. Halftime after 6 rounds, the match ends at 12 rounds or on 3 mascots, and harming a player is a foul.

Zone lines at x = 16.

| Type | x | y | w | h | Label |
|---|---|---|---|---|---|
| Feature / cover | 13 | 3 | 6 | 3 | Tower A |
| Water | 12 | 5 | 1 | 2 |  |
| Water | 19 | 5 | 1 | 2 |  |
| Water | 11 | 6 | 1 | 2 |  |
| Feature / cover | 13 | 6 | 6 | 1 | Restore deck A |
| Water | 20 | 6 | 1 | 2 |  |
| Difficult terrain | 8 | 7 | 2 | 1 |  |
| Water | 10 | 7 | 1 | 2 |  |
| Difficult terrain | 12 | 7 | 3 | 3 |  |
| Feature / cover | 15 | 7 | 4 | 1 |  |
| Water | 21 | 7 | 1 | 2 |  |
| Water | 9 | 8 | 1 | 1 |  |
| Difficult terrain | 11 | 8 | 1 | 3 |  |
| Difficult terrain | 15 | 8 | 1 | 2 |  |
| Feature / cover | 16 | 8 | 2 | 8 |  |
| Feature / cover | 19 | 8 | 1 | 1 |  |
| Water | 22 | 8 | 1 | 1 |  |
| Difficult terrain | 10 | 9 | 1 | 1 |  |
| Feature / cover | 18 | 9 | 1 | 1 |  |
| Floor | 20 | 9 | 1 | 1 | 2. Dry dirt |
| Difficult terrain | 12 | 10 | 1 | 3 |  |
| Difficult terrain | 13 | 10 | 1 | 1 | 1. Thick mud |
| Difficult terrain | 14 | 10 | 1 | 6 |  |
| Feature / cover | 15 | 10 | 1 | 3 |  |
| Feature / cover | 19 | 10 | 1 | 3 |  |
| Difficult terrain | 13 | 11 | 1 | 5 |  |
| Water | 5 | 12 | 1 | 18 |  |
| Water | 6 | 12 | 1 | 1 |  |
| Feature / cover | 18 | 12 | 1 | 8 |  |
| Water | 25 | 12 | 2 | 1 |  |
| Water | 4 | 13 | 1 | 16 |  |
| Difficult terrain | 15 | 13 | 1 | 3 |  |
| Water | 26 | 13 | 2 | 16 |  |
| Difficult terrain | 6 | 14 | 3 | 2 |  |
| Feature / cover | 9 | 14 | 3 | 1 |  |
| Difficult terrain | 12 | 14 | 1 | 1 |  |
| Feature / cover | 19 | 14 | 1 | 6 |  |
| Feature / cover | 22 | 14 | 4 | 1 |  |
| Difficult terrain | 9 | 15 | 1 | 3 |  |
| Feature / cover | 10 | 15 | 3 | 1 |  |
| Feature / cover | 23 | 15 | 3 | 1 |  |
| Water | 6 | 16 | 1 | 1 |  |
| Difficult terrain | 7 | 16 | 1 | 5 |  |
| Difficult terrain | 8 | 16 | 1 | 2 |  |
| Difficult terrain | 10 | 16 | 1 | 2 |  |
| Feature / cover | 11 | 16 | 6 | 1 |  |
| Feature / cover | 21 | 16 | 1 | 8 |  |
| Feature / cover | 25 | 16 | 1 | 1 |  |
| Difficult terrain | 6 | 17 | 1 | 4 |  |
| Difficult terrain | 11 | 17 | 1 | 3 |  |
| Feature / cover | 12 | 17 | 6 | 1 |  |
| Feature / cover | 22 | 17 | 2 | 7 |  |
| Feature / cover | 8 | 18 | 3 | 6 |  |
| Difficult terrain | 12 | 18 | 3 | 3 |  |
| Feature / cover | 15 | 18 | 3 | 1 |  |
| Feature / cover | 20 | 18 | 1 | 1 |  |
| Feature / cover | 25 | 18 | 1 | 1 |  |
| Difficult terrain | 15 | 19 | 1 | 2 |  |
| Feature / cover | 17 | 19 | 1 | 2 |  |
| Water | 11 | 20 | 1 | 1 |  |
| Feature / cover | 16 | 20 | 1 | 6 |  |
| Feature / cover | 20 | 20 | 1 | 1 |  |
| Feature / cover | 15 | 21 | 1 | 4 |  |
| Water | 18 | 21 | 3 | 4 |  |
| Water | 24 | 21 | 2 | 7 |  |
| Feature / cover | 17 | 22 | 1 | 5 |  |
| Feature / cover | 14 | 23 | 1 | 3 |  |
| Feature / cover | 13 | 24 | 1 | 4 |  |
| Water | 21 | 24 | 3 | 5 |  |
| Floor | 9 | 25 | 1 | 1 | 3. Garden |
| Floor | 18 | 25 | 1 | 1 | 4. Pond |
| Water | 19 | 25 | 2 | 1 |  |
| Water | 6 | 26 | 1 | 2 |  |
| Feature / cover | 12 | 26 | 1 | 2 |  |
| Feature / cover | 18 | 26 | 1 | 2 |  |
| Water | 20 | 26 | 1 | 1 |  |
| Feature / cover | 11 | 27 | 1 | 1 |  |
| Water | 15 | 27 | 2 | 1 |  |
| Feature / cover | 19 | 27 | 1 | 3 |  |
| Water | 16 | 28 | 2 | 6 |  |
| Feature / cover | 20 | 28 | 1 | 2 |  |
| Water | 24 | 28 | 1 | 1 |  |
| Water | 6 | 29 | 1 | 2 |  |
| Water | 18 | 29 | 1 | 5 |  |
| Feature / cover | 21 | 29 | 1 | 1 |  |
| Water | 22 | 29 | 2 | 1 |  |
| Water | 25 | 29 | 2 | 1 |  |
| Water | 7 | 30 | 1 | 2 |  |
| Water | 19 | 30 | 4 | 1 |  |
| Water | 24 | 30 | 2 | 1 |  |
| Water | 8 | 31 | 1 | 2 |  |
| Water | 19 | 31 | 3 | 1 |  |
| Water | 23 | 31 | 2 | 1 |  |
| Water | 9 | 32 | 1 | 2 |  |
| Water | 19 | 32 | 2 | 1 |  |
| Water | 22 | 32 | 2 | 1 |  |
| Water | 10 | 33 | 1 | 2 |  |
| Water | 19 | 33 | 1 | 1 |  |
| Water | 21 | 33 | 2 | 1 |  |
| Water | 11 | 34 | 1 | 2 |  |
| Feature / cover | 13 | 34 | 6 | 1 | Restore deck B |
| Water | 20 | 34 | 2 | 1 |  |
| Water | 12 | 35 | 1 | 2 |  |
| Feature / cover | 13 | 35 | 6 | 4 | Tower B |
| Water | 19 | 35 | 2 | 1 |  |
| Water | 19 | 36 | 1 | 1 |  |

| Token | Name | Side | Square |
|---|---|---|---|
| `K` | Kairos | Ally (teal) | `R17` |
| `M` | Mabli | Lorehold | `U17` |
| `J` | Juno | Witherbloom | `U16` |
| `Q1` | Quandrix guard | Ally (teal) | `S11` |
| `Q2` | Quandrix guard | Ally (teal) | `U11` |
| `m` | Quandrix mascot | Object | `P5` |
| `T` | Theodric | Silverquill | `R28` |
| `S` | Saffi | Prismari | `U28` |
| `R` | Rennick | Witherbloom | `H28` |
| `O1` | Opponent guard | Silverquill | `X31` |
| `O2` | Opponent guard | Silverquill | `V33` |
| `o` | Opponent mascot | Object | `P37` |

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
