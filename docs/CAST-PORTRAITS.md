# Cast portrait briefs — the 13 with no art

Where to find a token for the main cast, and what to ask an image model for when
you commission one.

## THE SHORT ANSWER

**You cannot find these tokens.** They are campaign-original, so no collection
draws them. Verified: all 13 carry `"how": null` in the vault's own
`atlas-vtt/statblock-art.json`, and the 115-file hearden pack in
`display/tokens/` is a Strixhaven set — it cannot contain people who do not
exist in Strixhaven.

**So generate them, and this is the one art problem here that is legally clean.**
The 5e-bits SRD portraits had to stay out of git because they are someone else's
unlicensed work. A generated portrait of *your* character is your own: you own
the character, and the output belongs to you. Same technique, opposite posture —
so these can be committed, unlike anything in `display/tokens/`.

**Generating is also cheaper than the alternative.** Commissioning 13 portraits
is real money. Of the 13, three are non-humanoid enough that a stock "fantasy
character portrait" model will get them badly wrong:

| Character | What a model will get wrong |
|---|---|
| **Vess** | A *huge* fiend whose face is turning ledger pages |
| **Ninefold** | A *Gargantuan* celestial whose face is an absence |
| **Tam** | A gorgon whose eyes do not blink and whose gaze does *not* petrify |

## WHAT THE CAMPAIGN ALREADY FIXED, AND WHAT IT DID NOT

Good news: this vault is better at physical description than it looks. All 11
`npc-files/*.md` share a template carrying a **`Visual motif:`** line and a
**`**Physical tells:**`** bullet, and the prose scatters real anchors — species,
clothing colour, a scar, a disability.

Bad news, and it matters: **seven of the thirteen have no recorded hair, eye or
skin colour anywhere.** The vault is written from the inside, in behaviour and
voice, and appearance is almost never the thing being described.

That is not a gap to paper over silently. It is a set of decisions, and they are
*yours* — the same way `prose/PLUMAGE.md:59` says Kairos needs "one plain physical
fact" given to him deliberately. Anything below marked **[chose]** is a
suggestion, not something I found in the vault.

## THE ONE HARD CONSTRAINT

**Selwyn Pike's race is under an explicit standing prohibition.**

> "Race deliberately unspecified — DO NOT ASSIGN, in any edition pass." —
> `npcs-full.md:198`, mirrored in `statblocks.json:74`

The reason is load-bearing: the Hourless forger must not be kenku, or the 3.5
unmasking reads as being about Ysolde's race instead of her choice. Drawing a
face for Pike risks the same leak, by image instead of by text.

His brief is below and he is on the list anyway, but he is the one to look at
twice. If you want a portrait for him, the safest version serves the design
rather than fighting it: **"forgettable and near"** (`research/02-students-of-strixhaven.md:136`)
is his function, and a distinctive portrait defeats the character.

---

# THE BRIEFS

Each is: what the vault states, what is missing, and a prompt you can hand to an
image model. Keep the stated part separate from the **[chose]** part — the first
is canon you must not contradict, the second is invention you may revise.

### Kairos — the PC
**Stated.** Kenku, raven (`characters/Kairos.md:5`, `prose/PLUMAGE.md:65`).
**Black-plumaged** — the campaign's load-bearing fact (`prose/PLUMAGE.md:3`). Fifteen
(`SCENE-0B-HISTORY.md:887`), he/him. Grey first-year uniform, no college pin
(`characters/Kairos.md:125`); later a Lorehold-green scarf, badly knitted, and
Quandrix colours with one Lorehold stripe. A white feather pen becomes a silver
quill; a spellbook with an owlin flight feather in the spine. A notably mobile
beak (`npc-files/petra-lune.md:100`).
**Load-bearing physics.** *"dirt, chalk, mortar, blood, wet. Black does not show
it"* (`prose/PLUMAGE.md:57`); *"chalk shows on white and vanishes on black"* (`:22`).
**Missing.** Height, build, eye colour, feather detail. `prose/PLUMAGE.md:55` admits it
outright: *"The prose never describes Kairos's plumage. Not once, in any file."*
**[chose]** wiry, slight, a youth not a man; beady dark eyes; soft sleek black
feathers; a scholar's stoop he hasn't grown out of.
> *Portrait of a 15-year-old kenku — a feathered raven-folk youth — sleek
> soft black plumage, beady dark eyes, a black beak, wiry and slight, wearing a
> grey first-year student robe with no college pin and a hand-knitted moss-green
> scarf, holding a silver quill. Muted library light, chest-up, painterly.*

### Ysolde Marrow — the Stopped Hand
**Stated.** Human, Quandrix probability theorist (`answer-key.md:32`). She/her.
Barefoot in summer (`npc-files/ysolde-marrow.md:88`); a real face that is *"tired
and kind"* (`source/3.5.md:14`); *"her eyes went past the room"* (`prose/CH-4.4-into-the-snarl.md:281`).
Draws in light while she talks. Fingers tap a count when stressed or lying.
**The mask** — this is the portrait most people will actually see: a plain white
oval, a drawn circle where the face should be, and **no hands on the dial**
(`prose/CH-3.5-the-stopped-hand.md:25`). Bell-shaped cloak, hem on the tiles, and **"no height and no
build to it at all"** — do not give her a silhouette (`prose/CH-1.5-finals-in-the-fractal-conservatory.md:83`).
**Missing.** Age, hair, eyes, build, skin. Everything.
**[chose]** fifties, silver-streaked dark hair pinned up, tired warm face, ink
stains on the fingers. Barefoot in the Paradox Gardens, laughing, drawing a
chimera in light.
> *Two portraits needed. (1) A woman in her fifties, silver-streaked dark hair
> loosely pinned, a tired and kind face, barefoot on a lawn, laughing, conjuring
> delicate luminous line-work in the air beside her. (2) A tall featureless
> bell-shaped cloak with its hem on stone tiles, and a smooth white oval mask
> bearing a drawn clock circle with no hands. Painterly, muted.*

### Hesper Vael — Magister
**Stated.** Owlin, Lorehold archaeomancer (`npc-files/hesper-vael.md:2`).
**White-plumaged** (`prose/PLUMAGE.md:3`) — and this is her whole visual
premise, the reason she has no bar in a black-and-white hall. She/her. A woman
in her forties (`prose/CH-4.5-the-unwritten-hour.md:215`). **Small**, with a **bad left shoulder**
(`prose/CH-4.4-into-the-snarl.md:49`, `:235`). Smooth facial disc. Grey Magister's robe, never
faculty colours. A Magister's chain. A battered field hat. A Lorehold-green
scarf she knitted badly. **Chalk and mortar in the seams of her shoulder
feathers**, which she never brushes off (`prose/SCENE-0B:163`).
**The feather code** (`npc-files/hesper-vael.md:30`): ruffled for fear or lying,
flat for grief, one quill lifting and settling for hurt, chest puffed for pride.
Beak opens slightly when about to tell truth but thinks better. Talons grip the
armrest.
**Missing.** Eye colour, exact facial disc pattern.
**[chose]** wide dark eyes, a heavy brow, feathers worn slightly flat at the
shoulder.
> *Portrait of a white-plumaged owlin — a feathered owl-folk woman in her
> forties — petite, a smooth round facial disc, wide dark eyes, a heavy brow,
> wearing a plain grey Magister's robe and a heavy chain of office. Chalk dust
> caught in the small feathers of one shoulder, unbrushed. One wing hooked over
> the chair back. Cool owl-roost light, chest-up, painterly.*

### Juno Ashvale
**Stated.** **Species conflict in the vault**: `answer-key.md:195` says human,
`prose/CH-1.1-orientation-night.md:63` says *"A halfling, sleeves rolled."* Worth settling before
commissioning. She/her. **Small-framed** (`npc-files/juno-ashvale.md:16`).
**Small, pale, calloused, scar-mapped hands** from peat-cutting and leech-farming
(`:30`). Hair in a knot that is losing; a braid over one shoulder; she braids her
hair when thinking. **A tally-mark scar on her wrist, fresh each week** — and it
heals to the colour of her hand (`answer-key.md:139`). Witherbloom-bound. Sleeves
always rolled.
**Props.** A jar of leeches named Socrates, Hypatia and Diogenes; a whetted
knife; a vial that smells of the fens.
**Tells.** Hands move while talking, mapping space; voice goes up at the ends of
sentences when frightened.
**Missing.** Hair colour, eye colour, height.
**[chose]** dark brown hair, sun-weathered, wiry; height 4'10"; a work-rough
handsomeness that does not match her laugh.
> *Portrait of a small-framed halfling herbalist student, late teens, dark
> sun-weathered hair in a losing knot, sleeves rolled, small pale calloused hands
> scarred with fresh tally marks, holding a glass jar of leeches. Ink-green
> greenhouse light, chest-up, painterly.*

### Mabli Quenn
**Stated.** Dwarf, from a dwarven clan of archivists and diggers
(`answer-key.md:196`). She/her. **Small and precise** (`prose/CH-1.1-orientation-night.md:67`).
*"a very small hand"* (`prose/CH-4.5-the-unwritten-hour.md:197`). Lorehold-bound first-year.
**Props.** Owl-feather wax seals, a satchel, a list, a clipboard.
**Tells.** Counts on her fingers when anxious and **loses count when terrified**;
squares paper stacks under stress.
**Missing.** Hair, eyes, skin, clothing colour, beard. Nothing.
**[chose]** dark braided hair pinned up, wire spectacles pushed down her nose,
damp bog-earth under her nails.
> *Portrait of a small precise dwarven student-archivist, dark braided hair pinned
> up, wire spectacles pushed down her nose, damp earth under her fingernails,
> clutching a wax-sealed letter sealed with a single owl feather, satchel strap
> across her chest. Warm archive lamplight, chest-up, painterly.*

### Theodric Vane
**Stated.** Elf, Silverquill first-year, aristocratic (`answer-key.md:197`).
He/him. Age **deliberately unspecified** — *"keep it vague or avoid 'eighteenth
birthday' framing"* (`npc-files/theodric-vane.md:185`). **Silverquill blue**
robes, immaculate, and he looked at the collar (`prose/CH-1.1-orientation-night.md:33`, `:75`).
**Tells.** Closes his hand into a fist when suppressing emotion; his jaw
tightens when lying; under extreme stress his fingers drum against his leg **in
fours**. Stands with his hands behind his back, in the manner of a man being
photographed against his will.
**Prop.** A Vane messenger-quill, turning above his open hand.
**Missing.** Everything visual. Height, build, hair, eyes, skin.
**[chose]** tall, narrow, immaculate; pale ash hair kept too long; the posture
above is the whole performance.
> *Portrait of a tall elven student in immaculate Silverquill-blue academic
> robes, pale ash hair grown slightly too long, narrow and over-posed, hands held
> behind his back, chin very slightly lifted, a silver messenger-quill turning
> above his open palm. Cool hall light, chest-up, painterly.*

### Dace Orrin
**Stated.** **Tiefling**, Witherbloom (`answer-key.md:202`); his tiefling
features are explicitly left for the DM (`npc-files/dace-orrin.md:142`). He/him.
Has taught at Witherbloom for most of twenty years. **A tall man in Witherbloom
grey** (`prose/CH-1.5-finals-in-the-fractal-conservatory.md:203`). **A tail** that flicks when a student says something
stupid (`:85`).
**Prop.** An essence gauge — a brass-and-glass instrument carried in a case at
his hip, its glass taking a yellow-green shadow unrelated to the room light.
**Tells.** Goes perfectly still when holding something back; taps the gauge case
twice before bad news; will not look at the bog's deep water.
**Missing.** Horns? Which tail? Everything else.
**[chose]** horns, small and worn; black-red; a lined, tired face; the tail is the
one thing that moves.
> *Portrait of a tall tiefling professor in his forties, small worn horns, a lined
> tired face, dressed in plain Witherbloom-grey robes, holding a brass-and-glass
> scientific instrument whose chamber glows a faint yellow-green. A long tail
> visible behind him. Flat institutional light, chest-up, painterly.*

### Vess the Tallykeeper
**Stated.** **Daemogoth** — huge, antlered, lichen-coated, with a face that is a
slowly turning ledger of pages (`source/2.3.md:10`). **Huge fiend, CR 10.**
Pronouns **it/its**. The Tally: *"a face that is not flesh but constantly turning
pages... each page marks names, dates, debts, and payments"*
(`npc-files/vess-tallykeeper.md:29`). Pages turn left to right, with the slow
precision of an accountant's fingers. **Antlers gain weight as it feeds**, growing
toward Daemogoth Titan. Bog water, ink-dark, pages drifting like leaves.
**Missing.** Almost nothing — this is the best-specified subject here. Palette of
the lichen and the pages.
**[chose]** grey-green lichen; pages the colour of old paper, ink script
legible-but-illegible; the antlers of something that has been standing in a bog
for a very long time.
> *A huge antlered fiend standing in ink-dark bog water, its body crusted in
> grey-green lichen, its face not flesh but a slow-turning ledger of yellowed
> pages covered in ink script. Vast mossy antlers, pages drifting like leaves
> around it. Cold, quiet, unsettling. Full figure, painterly.*

### Ninefold
**Stated.** An **Archaic** bound to the Biblioplex, a temporal being
(`npc-files/ninefold.md:2`). **Gargantuan**, celestial, AC 20, HP 245
(`Bestiary/Archaic.md:13`). Pronouns **it/its**. **Tall and faceless**, robed in
a script that will not hold still (`prose/CH-1.4-midterms-and-the-missing-girl.md:241`).
**The face — the whole design:** *"'face' is absence where light behaves
strangely"* (`npc-files/ninefold.md:93`). Robes shift continuously with script —
words, images, or the mathematics of time. Frightened, the script becomes
unreadable and spills like ink. Speaking true, painful things, the robes darken
to old paper. Shelves bend wrong around it; shadows fall at impossible angles.
**Missing.** Almost nothing.
**[chose]** the absence should read as a *hole in the light* — a lens, not a void
mask; keep the face as the least defined thing in the frame.
> *A towering, faceless, enormously tall figure robed in robes that are covered
> in continuously shifting script — words, diagrams and drifting mathematical
> notation that will not hold still. Where a face should be there is only an
> absence where the light bends wrongly. Around it, shelves bend and shadows fall
> at impossible angles. Old-paper sepia and ink. Painterly, unsettling, quiet.*

### Petra Lune
**Stated.** **Species is never stated anywhere in the vault** — the one gap that
outright contradicts nothing but leaves the most. She/her. Second-year in Y1, so
the oldest student present. Prismari-bound, out of a dye-works district; *"she
learned colour as chemistry before she learned her letters"*
(`npc-files/petra-lune.md:25`).
**The hands — her whole portrait:** **paint-stained to the wrist, vermilion in the
creases of one hand and brown in the other** (`prose/CH-1.4-midterms-and-the-missing-girl.md:19`); paint under her
nails four years later, and nobody has told her to stop. Sleeves rolled up and
forever slipping down. A paint-smudged scarf under a good coat. A pin moved from
mouth to hair.
**Tells.** When she lies or is frightened, **her hands go completely still, which
on her is alarming.** She tilts her head at a garment the way a hawk tilts at a
mouse.
**Prop.** A sketchbook; the Displacement Cape.
**Missing.** Species, hair, eyes, height, build.
**[chose]** human, or half-elf for the art affinity; dark hair permanently
flecked with pigment; slight, never still.
> *Portrait of a young art student, dark hair permanently flecked with flecks of
> pigment, sleeves rolled up and slipping down, a paint-smudged scarf under a
> good coat, hands held up and completely still — stained to the wrist, vermilion
> in the creases of one hand and umber in the other. Studio light, chest-up,
> painterly.*

### Ambrin Hollis — Coach
**Stated.** Dwarf (`answer-key.md:203`), **he/him** (`source/2.2.md:7`).
**Gruff, cheerful, loud, missing two fingers** — the most-repeated fact about him
in the vault, four cites, and **no beard is ever mentioned despite "dwarf"**.
Takes stairs *four at a time* (`prose/CH-4.5-the-unwritten-hour.md:47`) and shakes hands slightly too
long, which from him is an embrace.
**Prop.** A single battered manuscript, *Fractal Selves in Motion*, which he
teaches from. Has lost his own echo in the invasion and can no longer echo-strike.
**Missing.** Which hand, how they were lost, hair, eyes, height, clothing. He has
no `npc-file` at all — the thinnest of the thirteen.
**[chose]** a heavy greying beard, because a dwarf without one will read as an
oversight; stocky; the missing fingers on the hand he rests on the manuscript.
> *Portrait of a stocky dwarven athletics coach in his later years, heavy
> greying beard, one hand missing two fingers and resting on a battered
> manuscript, the other gesturing mid-sentence. Broad, loud, cheerful despite
> himself. Warm stadium light, chest-up, painterly.*

### Tam, Observant Sequencer
**Stated.** **Gorgon**, second-year Quandrix (`source/1.2.md:58`). She/her.
**Her eyes are the design:** they do not blink enough; very round when surprised;
she watches without blinking. **They do not petrify** — *"They just make people
very uncomfortable"* (`npc-files/tam.md:147`). **One temple scale catches light
when afraid** (`:106`) — the only confirmed gorgon body feature.
**Tells.** Fingers flat on the table when listening, curling when lying; jaw tight
when her work is dismissed; pen pressure rises with emotion. *"Intelligence
shows in how still she goes, like predatory calm."*
**Prop.** A lab journal she guards.
**Missing.** Eye colour (deliberately never given), scale coverage, height,
build, hair.
**[chose]** do **not** give her snake hair — that is Medusa's read, and the
campaign explicitly says the gaze is not the weapon. Scales at the temples and
along the jaw only; eyes a flat pale gold, unblinking.
> *Portrait of a gorgon student, calm and watchful, flat pale-gold eyes that do not
> blink, fine scales at the temples and along the jaw, no snakes in her hair. She
> is very still, fingers laid flat on a table, a guarded lab journal under one
> hand. Cool Quandrix light, chest-up, painterly.*

### Selwyn Pike — the Second Hand ⚠
**Stated.** He/him. Lawful (`prose/CANON-AUDIT.md:65`). **Demeanor: helpful,
forgettable** (`NPCs/Selwyn Pike.md:18`). The design requirement, stated as such:
*"Pike works because he is forgettable and near"*
(`research/02-students-of-strixhaven.md:136`). Silver-quill ink, a fractionally
late downstroke. Wears a **blank clock-face mask** — plain white, a drawn circle
where the face went, no hands on it either (`prose/CH-4.4-into-the-snarl.md:143`).
**⛔ Race: DO NOT ASSIGN** (`npcs-full.md:198`, `statblocks.json:74`).
**Missing.** Everything, by order.
> *Portrait of a forgettable young teaching assistant in plain grey student
> robes — deliberately unremarkable, mid-background, nothing to look at twice —
> holding a silver quill. Muted, low-contrast, slightly washed out. The point is
> that you would not remember this face.*

---

## HOW TO GET THEM

**Generate, in this order of difficulty.** Of these 13, the first three are one
prompt, the next four are a human in a robe, and the last six are the work.

1. **Ninefold, Vess** — the two non-humanoid ones. Most image models will fail
   these; expect to iterate, or to generate the robe/body and paint the face in.
2. **Hesper, Tam** — an owlin and a gorgon. Both are specific enough to prompt
   precisely, and both have a *feature* (feathers, scales) a model handles well.
3. **Kairos** — a kenku. Doable, and the black plumage is the point.
4. **Juno, Mabli, Theodric, Orrin** — humans and a dwarf in a robe. A generic
   fantasy portrait prompt plus two or three stated anchors each.
5. **Ysolde, Petra, Hollis, Selwyn** — need the invention decisions above first.

**The pipeline already exists in pieces.** `install_srd_art.py` knows how to
fetch, validate, resize to 256px on the long edge and land art in a pool. What
is missing is a generate-then-install path, because no image-generation tooling
is in this repository and none should be — the model choice and the credentials
are yours, not the engine's.

**Two rules if you do generate them.**

Put them in `display/cast/` — a *new* pool, and the first one that is yours
rather than third-party. It is committable, unlike `display/tokens/` and
`display/srd-art/`, and that distinction is the whole reason the licensing
posture is different for this art than for the other 334 portraits.

Name each file by the slug the matcher already uses — `hesper-vael.png`,
`selwyn-pike.png` — and they will match on the first run, exactly, with no table
and no approval. `statblock_art.py` will pick them up with no code change once
the pool is registered in `POOL_DIRS`.

**One thing worth deciding first:** seven of these briefs end in invention. If you
would rather not invent — if you would prefer the main cast to stay honestly
unpainted than wrongly painted — then `--fill-generic` already covers the
supporting cast, and the right answer for the thirteen is that they have no art
until someone draws them. That is a legitimate position and the report prints it
without complaint.
