"""puppet_lens_detector.py: does the DM put words, feelings or choices in the PC's mouth?

Support module for the puppeting lens. Not a test module itself, and not product
code: it is the measuring instrument. It lives in tests/ beside localdm_fakes.py
and tactics_fixtures.py for the same reason those two do, and it is imported by
tests/test_puppet_lens.py (this repo, no model, no network) and by the harness
../../../test_puppet_lens.py, which lives in the parent dnd-gm repo next to
test_dice_lens.py. Putting it under scripts/ would make it shipped code, and an
experiment that ships is a feature nobody asked for.

WHAT IS BEING MEASURED
======================

PUPPETING is the narrator writing the player's character's dialogue, actions or
feelings, which the player never chose. It is not railroading. Railroading is
about the world moving; puppeting is about the character moving on their own.
The distinction matters because the two have opposite fixes: railroading wants
a wider world, puppeting wants a quieter DM.

The reason it is worth a lens at all: the guardrail is an arbiter advisor that
returns a NOTE, which rebuilds the retry task and corrects the DRAFT. The engine
is not in the path that produces character dialogue, so nothing refuses at the
source. The guardrail may be decorative. Field research calls puppeting the most
underrated failure in the category, and its worst form is not cosmetic: a player
who lies to an NPC without telling the DM gives the model an intent to infer,
and a wrong inference writes the lie into the PC's mouth.

So the lens answers one question with evidence: given a player line and the reply
the player actually saw, does the reply puppet? Four shapes:

  invented-dialogue   the PC speaks in quotation marks, in words the player did
                      not type
  assigned-emotion    the PC is told what they feel
  narrated-action     the DM narrates the PC doing something unstated
  pre-resolved        the player states an intent and the reply carries the
                      attempt AND its outcome in one beat

WHY THE DETECTION IS DELIBERATELY WEAK
======================================

A trip rate is only meaningful if the instrument is trustworthy, and the
trustworthiness of a text detector lives entirely in its false-positive rate. A
lens that flags "You feel the cold stone" is a lens whose rate is a measurement
of the regex, not of the DM. So every rule here is written to miss rather than
to shout, and the resulting number is a LOWER BOUND on the real trip rate. That
bias is stated in the report rather than hidden, because a lower bound is
reportable and a noise floor is not.

What was tried first and why it was thrown away, in order:

1. A flat keyword list ("you say", "you feel", "Kairos thinks"). It flags the
   DM's own NPC dialogue, the player's own typed dialogue, and every question
   the DM ends a beat with ("What do you do?"). Every one of those is correct
   narration. Killed by the negative controls.

2. Flagging any quotation mark in the reply. The DM quotes sign text, spells,
   book titles and NPC lines, and the player quotes themselves. Killed by the
   attribution requirement below: a quote only counts once a speaker is
   identified, and an unidentified quote never counts.

3. Comparing the quoted text to the player's line word for word. The DM
   legitimately reuses the player's own words when it echoes them, so exact
   matching misses the echo. Replaced by a token-overlap ratio against the
   player's line, with a threshold rather than an equality test.

4. Any second-person action verb as a puppeting tell. This was the worst one and
   it is worth recording why: "you flinch", "you pause", "you breathe" are the
   DM narrating involuntary consequences of what the player chose, which is
   correct and necessary. Separating the discretionary verbs (draw, open, follow,
   hand over) from the involuntary ones (flinch, pause, breathe) is the whole
   content of rule 3.

5. Flagging bare third-person pronouns ("he draws his blade"). An NPC does that
   too, and the lens has no reliable way to tell which "he" it is reading. Only
   the PC's proper name is used in third person, and only because the name is
   supplied by the caller rather than guessed.

THE FOUR FALSE POSITIVES THAT MATTER MOST
=========================================

  the PC's name          "Kairos?" and "Come here, Kairos" are the DM using a
                         name, not the PC speaking. Name tokens are removed
                         before the word-absence test, and a quote left with no
                         content words is never flagged.
  the player's own words "I tell her 'I only want the catalogue'." followed by
                         '"Only the catalogue," you say.' is the DM being
                         faithful. Handled by the overlap ratio, and it is why
                         the threshold is 0.5 and not 0.
  the DM's NPC speech   "The archivist says, 'State your business.'" is the
                         single most common quotation in the genre. Attribution
                         is resolved to a speaker and only a PC speaker counts.
  an offered choice      "You could step back." and "Do you draw your blade?"
                         are the DM handing the turn back, which is the opposite
                         of the defect. Sentences ending in a question mark and
                         clauses containing an offer are excluded from rules 2
                         to 4.

TWO KNOWN MISSES, RECORDED RATHER THAN PATCHED
==============================================

  An unattributed quote. '"Not again."' on its own line, with no speaker cue in
  the same sentence, is not counted. It is usually the PC, and guessing is how
  a lens becomes noise. Fixing it needs a turn-level attribution the reply
  format does not carry.

  A quote containing a full stop. Sentence boundaries are computed on a masked
  copy so that "Mr. Vale sent me." is not split, but a quote that also carries
  the attribution across the boundary still loses its speaker. Rare, and a miss
  is the correct direction.

A NOTE ON ROLES
===============

This detector is deliberately NOT reply.speaks_for_player, and does not call it.
That guardrail is the thing under test: measuring puppeting with the shipped
guardrail would measure the guardrail with itself and report a rate of zero by
construction. The two overlap heavily on second-person speech, and the lens adds
the third-person case (Kairos says / Kairos feels) that the guardrail misses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── vocabulary ────────────────────────────────────────────────────────────────

#: The four shapes, in the order the report counts them.
KINDS = ("invented-dialogue", "assigned-emotion", "narrated-action", "pre-resolved")

#: Words that carry no evidence of authorship. A quote made only of these cannot
#: be shown to be invented, so it is never flagged.
FUNCTION_WORDS = frozenset("""
a an and are as at be been but by can cant could did do does doesnt dont for from
had has have he her hers him his i if in into is isnt it its me my no nor not of on
or our ours out she so than that the their theirs them then there these they this
those to too up us was we were what when where which while who whom why will wont
with would you your yours yourself am shall
""".split())

#: Short utterances that ARE authorship evidence. "Yes." from a player who typed
#: "I nod" is the DM putting a word in the PC's mouth; "Yes." from a player who
#: typed "I tell her yes" is the DM being faithful. Both are one word, so they
#: cannot be left to the function-word filter.
INTERJECTIONS = frozenset("""
yes no yeah nope maybe please thanks thank sorry hello hi help stop wait never
indeed certainly absolutely
""".split())

#: Speech verbs. Used only to attribute a quotation to a speaker, never to
#: decide that speech happened.
SPEECH_VERB = (r"says?|asks?|repl(?:y|ies|ied)|answers?|whisper(?:s|ed)?|"
               r"mutter(?:s|ed)?|murmur(?:s|ed)?|adds?|continues?|insists?|"
               r"declares?|promises?|announces?|explains?|presses?|repeats?|"
               r"calls? out|shouts?|exclaims?|offers?|reads? aloud|bellows?|"
               r"rasps?|murmurs? to|utters?")

#: Emotion nouns. A sensation with no emotion word in it is the world acting on
#: the PC, which is the DM's job: "you feel the cold of the stone" is fair, and
#: "you feel a chill of recognition" is not. The difference is the noun, so the
#: noun is the test.
EMOTION_NOUN = (r"chill|chills|dread|terror|fear|horror|relief|unease|uneasiness|"
                r"anxiety|guilt|pride|shame|grief|sorrow|joy|hope|suspicion|doubt|"
                r"certainty|recognition|gratitude|longing|melancholy|panic|"
                r"trepidation|foreboding|kinship|comfort|tension|wariness|"
                r"resolution|conviction|curiosity|nostalgia|affection|hatred|"
                r"contempt|embarrassment|satisfaction|disappointment|remorse|"
                r"reassurance|dread|wrongness|familiarity|belonging")

#: Involuntary body reactions, used as emotion evidence but never as action
#: evidence. A chill in the spine is something the PC is given; a flinch is
#: something that happens to them.
BODY_METAPHOR = (r"heart\s+(?:pounds|races|leaps|sinks|clenches|hammers|skips|"
                 r"stops|jumps)|stomach\s+(?:drops|turns|knots|lurches|churns)|"
                 r"chest\s+(?:tightens|aches|cavities)|breath\s+(?:catches|"
                 r"quickens|stops|hitches)|(?:a\s+)?chill\s+(?:runs|creeps|spreads|"
                 r"crawls|shoots)|shiver|flush(?:es)?\s+to|colour drains|"
                 r"color drains|breath catches")

#: Cognition verbs that assign an interior state the player did not report.
#: "wonder" is absent: "You wonder what is in the stacks" is the DM handing the
#: turn back, and it reads as an offer. "know" IS here, which needs the note: a
#: generic-second-person "you never know how a fumble goes" cannot match, because
#: the pattern requires the verb directly after "you" and "never" sits between.
#: A bare "You know there is no name in the margin" is the DM deciding what the
#: PC has worked out, and a live run produced exactly that sentence while a
#: second-person generic could not be the false positive that removing "know"
#: was meant to prevent.
COGNITION = (r"knows?|knew|realiz(?:e|es|ed|ing)|realis(?:e|es|ed|ing)|suspects?|"
             r"suspected|remembers?|remembered|decides?|decided|understands?|"
             r"understood|cannot help but|can'?t help but|find yourself|"
             r"finds yourself|are sure|are certain|is sure|is certain")

#: Discretionary physical acts: things a player chooses. Every one of these in
#: the second person, unstated by the player, is a choice the DM made for them.
#:
#: The bare form is always listed alongside the inflected one ("pushes?|push(?:ed)?"
#: style). An earlier draft spelled only -s and -ed and silently missed every
#: present-tense singular, which is the form the loop's own prompts produce: a
#: live run flagged "you lean" and not "you push" or "you reach for", in the same
#: sentence, for the same defect.
DISCRETIONARY = (r"steps?|stepped|walks?|walked|runs?|ran|flees?|fled|retreats?|"
                 r"moves?|moved|draws?|drew|sheathe(?:s|d)?|unsheathe(?:s|d)?|"
                 r"reach(?:es|ed)?|grabs?|grabbed|seizes?|seized|takes?|took|"
                 r"picks? up|picked up|pockets?|pocketed|hands? over|handed over|"
                 r"offers?|offered|accepts?|accepted|refuses?|refused|opens?|opened|"
                 r"closes?|closed|locks?|locked|unlocks?|unlocked|shuts?|shut|"
                 r"bars?|barred|follows?|followed|turns? away|turned away|"
                 r"turns? back|sits? down|sat down|stands? up|stood up|"
                 r"bows?|bowed|nods?|nodded|smiles?|smiled|frowns?|frowned|"
                 r"laughs?|laughed|cries?|cried|weeps?|wept|sighs?|sighed|"
                 r"swallows?|swallowed|leans?|leaned|knocks?|knocked|"
                 r"push(?:es|ed)?|pull(?:s|ed)?|shoves?|shoved|drops?|dropped|"
                 r"puts? away|put away|writes?|wrote|reads?|read|casts?|cast|"
                 r"attacks?|attacked|strikes?|struck|fire(?:s|d)?|aims?|aimed|"
                 r"answers?|answered|replies?|replied|falls? back|fell back|"
                 r"steps? back|stepped back|steps? forward|stepped forward|"
                 r"steps? away|stepped away")

#: Involuntary and inert verbs. These are excluded from rule 3 on purpose: they
#: are consequences the player could not have chosen not to have, and narrating
#: them is the DM doing its job. Listed as whole phrases because the risk is
#: "you pause" being read as "you decide to pause".
INVOLUNTARY = (r"flinch(?:es|ed)?|gasp(?:s|ed)?|shiver(?:s|ed)?|tremble(?:s|d)?|"
               r"stagger(?:s|ed)?|wince(?:s|d)?|blink(?:s|ed)?|startl(?:e|es|ed)|"
               r"recoil(?:s|ed)?|sneeze(?:s|d)?|cough(?:s|ed)?|choke(?:s|d)?|"
               r"breathe(?:s|d)?|breathes|exhale(?:s|d)?|inhale(?:s|d)?|"
               r"pause(?:s|d)?|hesitat(?:e|es|ed)|stops?\b|stalls?|waits?|waited|"
               r"watch(?:es|ed)?|watches|listen(?:s|ed)?|looks?|looked|sees?|saw|"
               r"hears?|heard|notices?|noticed|observes?|study(?:ies|ied)|"
               r"studies|examin(?:e|es|ed)|inspect(?:s|ed)?|consider(?:s|ed)?|"
               r"think(?:s)?|thought|recall(?:s|ed)?|remembers?|sense(?:s|d)?|"
               r"smell(?:s|ed)?|focus(?:es|ed)?|linger(?:s|ed)?|remain(?:s|ed)?|"
               r"stands?\b|stand|stay(?:s|ed)?|exist(?:s|ed)?|appear(?:s|ed)?|"
               r"seem(?:s|ed)?|become(?:s)?|got|get|gets|"
               # Posture, not choice. "You lean against the sill" after "I stay by
               # the window" describes the position the player just took, and a
               # DM that leaves the PC standing like a coat rack has not written
               # for the player. Same reasoning as pause and stand, which are
               # already here.
               r"leans?|leaned|perch(?:es|ed)?|hunker(?:s|ed)?|settle(?:s|d)?|"
               r"prop(?:s|ped)?|brace(?:s|d)?|squat(?:s|ed)?|crouch(?:es|ed)?")

#: Marks that turn an action verb into something the player owns: an attempt, a
#: refusal, a conditional, or a question.
#: Compiled, not interpolated anywhere, so it is a pattern from the start.
ATTEMPT_MARKER = re.compile(
    r"tr(?:y|ies|ied|ying)|attempt(?:s|ed|ing)?|fail(?:s|ed)?|"
    r"manag(?:e|es|ed)|abl(?:e|es)|cannot|can'?t|could|would|will|"
    r"might|may|do you|did you|do you want|whether|if you|"
    r"refus(?:e|es|ed) to|without|about to|going to|prepare(?:s|d)?|"
    r"start(?:s|ed)?|begin(?:s|ning)?|ready to", re.I)

#: The player's own statement of intent. Rule 4 only fires when the PLAYER
#: framed the turn as an attempt, because a player who says "I open the door"
#: has already resolved it and the DM narrating the swing is correct.
PLAYER_INTENT = re.compile(
    r"tr(?:y|ies|ied|ying) to|attempt(?:s|ed)? to|see if|see whether|"
    r"find out whether|find out if|check whether|check if|"
    r"look(?:s|ing)? for|search(?:es|ing)? for|ask about|"
    r"tr(?:y|ies) and|wonder(?:s|ing)? if|hope to|try to work out|"
    r"try to figure out|ask whether|ask if|test whether|test if|"
    r"feel out|see if I can|try to remember", re.I)

#: An outcome, in the second person, that resolves the attempt. Paired with
#: ATTEMPT_IN_REPLY and the player's own intent to make rule 4 mean "the reply
#: answered the question the player asked", not "the reply used a success verb".
OUTCOME = (r"finds?|found|sees?|sees it|spots?|notices?|discovers?|reads?|"
           r"realiz(?:e|es)|realis(?:e|es)|understands?|succeeds?|succeeded|works?|"
           # "You know there is no name here" states a result as plainly as "you
           # find nothing", and the live run that produced the KNOW entry in
           # COGNITION produced it here too: a beat with no roll in it, where
           # the DM told the player what the margin does not contain. Without
           # this the pre-resolved trial read that reply as unresolved.
           r"knows?|knew|"
           r"budges?|gives|opens?|snaps?|clicks?|turns?|recoils?|relents?|"
           r"believes?|accepts?|nods?|grants?|admits?|confesses?|agrees?|"
           r"turns away|steps back|slips?|creaks?|grinds?|rattles?|falls?|holds?|"
           r"is right|was right|is wrong|was wrong|lies?|solves?|answers?")
_OUTCOME = re.compile(OUTCOME, re.I)

ATTEMPT_IN_REPLY = (r"tr(?:y|ies|ied|ying)|attempt(?:s|ed|ing)?|search(?:es|ed|ing)?|"
                    r"look(?:s|ed|ing)? for|pr(?:y|ies|ied|ying)|pick(?:s|ed|ing)?|"
                    r"forc(?:e|es|ed|ing)|listen(?:s|ed|ing)?|peer(?:s|ed|ing)?|"
                    r"examin(?:e|es|ed)|inspect(?:s|ed)?|ask(?:s|ed|ing)?|"
                    r"persuad(?:e|es|ed|ing)|sneak(?:s|ed|ing)?|slip(?:s|ed|ing)? past|"
                    r"reach(?:es|ed)? for|feel for|check(?:s|ed|ing)?|read(?:s|ing)?|"
                    r"stud(?:y|ies|ied)|open(?:s|ed|ing)?|lift(?:s|ed|ing)?|"
                    r"lean(?:s|ed|ing)?|ti(?:l|lt|lts|lled)|trace(?:s|d)?|trail(?:s|ed)?|"
                    r"scan(?:s|ned|ning)?|go over|work through|hunt(?:s|ed|ing)?|"
                    r"thumb through|turn the|leaf through|scan for")

NEGATION_BEFORE = re.compile(
    r"(?:\bdo(?:es)? not\b|\bdo ?n'?t\b|\bdid ?n'?t\b|\bnever\b|\bno longer\b|"
    r"\brefus(?:e|es|ed) to\b|\bwithout\b|\bunable to\b|\bnot\b)\s*$|"
    r"(?:\bdo(?:es)? not\b|\bdo ?n'?t\b|\bdid ?n'?t\b|\bnot\b|\bnever\b|"
    r"\bunable to\b)[^.!?]{0,24}$", re.I)

OFFER = re.compile(r"\b(?:could|would|might|do you|did you|do you want|do you dare|"
                   r"will you|would you|are you going to|your choice|if you|"
                   r"want to|shall)\b", re.I)

# ── finding ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Finding:
    """One piece of evidence. `kind` is one of KINDS."""

    kind: str
    evidence: str
    detail: str = ""
    span: tuple = field(default=(0, 0), compare=False)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "evidence": self.evidence, "detail": self.detail}


# ── tokenising ────────────────────────────────────────────────────────────────

_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def content_tokens(text: str, drop: frozenset = frozenset()) -> set:
    """The words in `text` that can carry evidence of who wrote it.

    Function words are dropped because they are shared by every sentence in the
    language: "I only want the catalogue" and "Only the catalogue," you say
    overlap on four of five tokens if "only" and "the" count, and the overlap
    ratio then says the player wrote it when they only supplied a noun. Names in
    `drop` go too, so a PC's own name never counts as the player having said it.
    """
    out = set()
    for w in _WORD.findall(text or ""):
        w = w.lower().replace("'", "")
        if len(w) < 2 or w in FUNCTION_WORDS or w in drop:
            continue
        out.add(w)
    return out


# ── quoted spans and attribution ──────────────────────────────────────────────

#: Straight and curly double quotes. Single quotes are not matched on purpose:
#: every contraction in the corpus is an apostrophe, and pairing those produces
#: spans that are not speech.
QUOTED = re.compile(r"[\"\u201c](?P<q>[^\"\u201c\u201d]{1,200}?)[\"\u201d]")

_SENTENCE_BREAK = re.compile(r"[.!?](?=\s|$)|\n+")

_PRONOUN = r"(?:you|yourself|he|she|it|they|who|someone|somebody|no one)"
_NOUN_PHRASE = r"(?:the|an?|that|this)\s+[A-Za-z' -]+?"
_PROPER = r"[A-Z][A-Za-z'-]+"
_SUBJECT = rf"(?:{_PRONOUN}|{_NOUN_PHRASE}|{_PROPER})"
#: Between the subject and the speech verb. The bare \s+ is load-bearing: without
#: it "you say" does not parse, because the quantifier below can only absorb
#: whole words, and the two shapes that matter are the bare one ("you say") and
#: the tagged one ("you say, quietly" / "Kairos says, low").
_GAP = r"\s+(?:\w+[,\s]+){0,2}?"
_ATTRIB = re.compile(rf"(?P<who>{_SUBJECT}){_GAP}(?:{SPEECH_VERB})\b")


def quoted_spans(text: str) -> list:
    """(start, end, inner) for every quoted span, in order.

    Endpoints are into the ORIGINAL text, so evidence can be sliced out of it.
    """
    return [(m.start(), m.end(), m.group("q")) for m in QUOTED.finditer(text or "")]


def _mask(text: str) -> str:
    """Same length as `text`, with the inside of every quote blanked.

    Sentence boundaries and keyword scans are computed on this, so that a full
    stop, a question mark or the word "you" inside a quotation cannot split or
    trip a rule. Offsets are preserved, so a match found in the mask maps back to
    the original.
    """
    chars = list(text or "")
    for s, e, _ in quoted_spans(text):
        for i in range(s + 1, e - 1):
            if chars[i] != "\n":
                chars[i] = "x"
    return "".join(chars)


def _clause_bounds(masked: str, pos: int) -> tuple:
    """(start, end) of the sentence containing `pos` in the masked text."""
    start = 0
    for m in _SENTENCE_BREAK.finditer(masked, 0, pos):
        start = m.end()
    end = len(masked)
    m = _SENTENCE_BREAK.search(masked, pos)
    if m:
        end = m.start()
    return start, end


def attribute(text: str, start: int, end: int, pc_names=()) -> str:
    """Who is speaking the quoted span at [start, end): "pc", "other" or "".

    "" means unidentified, and an unidentified quote is never counted. This is
    the rule that keeps the genre's most common line of dialogue ("The archivist
    says, 'State your business.'") out of the count, and it is deliberately
    asymmetric: identifying a PC costs nothing when wrong in the direction of a
    flag, so a PC named anywhere in the tag counts, while a bare "he" never does.
    """
    masked = _mask(text)
    lo, hi = _clause_bounds(masked, start)
    pcs = {n.lower() for n in pc_names}
    for lo_s, hi_s in ((end, hi), (lo, start)):
        clause = text[lo_s:hi_s]
        best = ""
        for m in _ATTRIB.finditer(clause):
            who = m.group("who").strip().lower()
            first = who.split()[0] if who.split() else ""
            best = "pc" if (who in ("you", "yourself") or first in pcs
                            or who in pcs) else "other"
        if best:
            return best
    return ""


# ── rule 1: invented dialogue ─────────────────────────────────────────────────

#: Fraction of a quotation's content words that must appear in the player's own
#: line before the lens accepts that the player wrote it. 0.5 rather than 1.0
#: because the DM legitimately compresses a long line of dialogue ("Leave me
#: alone," I tell him, turning away. -> "Leave me alone," you snap.) and a
#: strict test would flag the faithful echo. 0.5 rather than 0.0 because "Not
#: again." from a player who typed "I tell her not to come back" is the DM
#: choosing the words, and only half the tokens are shared.
UNWRITTEN_RATIO = 0.5


def unwritten(quote: str, player_line: str, pc_names=()) -> bool:
    """True when the player could not have written this quotation.

    Decided on content-word overlap, never on character identity: a PC's name
    inside a quote is the DM using a name, and short interjections are compared
    like any other word because "Yes." and "I nod" are a real difference.
    """
    drop = frozenset(n.lower() for n in pc_names)
    q = content_tokens(quote, drop)
    if not q:
        return False                      # a name, or nothing but function words
    p = content_tokens(player_line, drop)
    return len(q & p) / len(q) < UNWRITTEN_RATIO


def dialogue_findings(player_line: str, text: str, pc_names=()) -> list:
    out = []
    for start, end, quote in quoted_spans(text):
        if attribute(text, start, end, pc_names) != "pc":
            continue
        if unwritten(quote, player_line, pc_names):
            ctx = " ".join(text[max(0, start - 40):end + 40].split())
            out.append(Finding("invented-dialogue", ctx,
                               f"PC speaks: {quote!r}", (start, end)))
    return out


# ── rule 2: assigned emotion ──────────────────────────────────────────────────

_EMOTION_FEEL = re.compile(
    rf"\b(?:you|your)\s+(?:feel|feels|felt|know|knows|knew|wonders?|wondered)\b"
    rf"[^.!?]{{0,90}}?(?:{EMOTION_NOUN}|{BODY_METAPHOR}|nothing|numb|empty)\b", re.I)
_EMOTION_COGNITION = re.compile(
    rf"\b(?:you|your)\s+(?:{COGNITION})\b", re.I)
_EMOTION_BODY = re.compile(
    rf"\b(?:{BODY_METAPHOR})[^.!?]{{0,40}}?\b(?:your|you)\b"
    rf"|\b(?:your|you)\b[^.!?]{{0,40}}?\b(?:{BODY_METAPHOR})\b", re.I)
_NAME = r"[A-Za-z][A-Za-z'-]*"
_EMOTION_NAME = re.compile(
    rf"\b{_NAME}\s+(?:{COGNITION}|feels?|felt|wonders?|wondered)\b"
    rf"|\b{_NAME}\s+(?:is|feels)\s+(?:{EMOTION_NOUN})\b", re.I)


def _name_patterns(pc_names) -> list:
    return [re.compile(rf"\b{re.escape(n)}\b", re.I) for n in pc_names]


def _is_question(clause: str) -> bool:
    return clause.rstrip().endswith("?")


def emotion_findings(text: str, pc_names=()) -> list:
    """Emotion or interior state assigned to the PC.

    Second person, or the PC's name. Bare "he" is never used: an NPC has one
    too, and a lens that guesses is a lens that measures its own guesses.
    """
    text = text or ""
    masked = _mask(text)
    names = _name_patterns(pc_names)
    out = []
    for pattern, kind in ((_EMOTION_FEEL, "feeling"), (_EMOTION_COGNITION, "cognition"),
                          (_EMOTION_BODY, "body")):
        for m in pattern.finditer(masked):
            lo, hi = _clause_bounds(masked, m.start())
            clause = text[lo:hi]
            if _is_question(clause) or OFFER.search(clause):
                continue
            if NEGATION_BEFORE.search(text[max(0, m.start() - 40):m.start()]):
                continue
            if names and not any(p.search(masked[max(0, m.start() - 30):m.end()])
                                 for p in names):
                if kind == "cognition" and not re.match(r"\s*you", text[m.start():], re.I):
                    continue          # "Kairos decides" is not a player subject
            out.append(Finding("assigned-emotion",
                               " ".join(clause.split())[:180],
                               f"assigned {kind}: {text[m.start():m.end()].strip()!r}",
                               (m.start(), m.end())))
    for m in _EMOTION_NAME.finditer(masked):
        if not names or not any(p.search(m.group(0)) for p in names):
            continue
        lo, hi = _clause_bounds(masked, m.start())
        clause = text[lo:hi]
        if _is_question(clause) or OFFER.search(clause):
            continue
        out.append(Finding("assigned-emotion", " ".join(clause.split())[:180],
                           f"third person, PC named: {m.group(0)!r}",
                           (m.start(), m.end())))
    return out


# ── rule 3: narrated action ───────────────────────────────────────────────────

_ACTION_SECOND = re.compile(
    rf"\byou\s+(?:{DISCRETIONARY})\b", re.I)


def _action_name(pc_names) -> "re.Pattern | None":
    """The third-person form, built per call because the name is the caller's.

    A pattern built at import time would have to hardcode Kairos, and a lens
    that only knows one character is a lens that reports zero on every other
    sheet.
    """
    if not pc_names:
        return None
    alts = "|".join(re.escape(n) for n in pc_names)
    return re.compile(rf"\b(?:{alts})\s+(?:{DISCRETIONARY})\b", re.I)


def _player_verb(player_line: str, verb: str) -> bool:
    """Did the player's own line use this verb, in any inflection?

    Compared on a crude stem because the DM freely re-inflects ("I nudge the
    door" -> "You ease the door open"). A missing this costs a flag on a
    faithful echo, which is why the stem is generous rather than exact.
    """
    stem = re.sub(r"[^a-z]", "", verb.lower())
    for suf in ("ed", "es", "s", "ing"):
        if stem.endswith(suf) and len(stem) - len(suf) >= 3:
            stem = stem[:-len(suf)]
            break
    if not stem:
        return False
    return any(w.startswith(stem[:max(4, len(stem) - 2)])
               for w in _WORD.findall(player_line or ""))


def action_findings(player_line: str, text: str, pc_names=()) -> list:
    text = text or ""
    masked = _mask(text)
    out = []
    third = _action_name(pc_names)
    for pattern in [_ACTION_SECOND] + ([third] if third else []):
        for m in pattern.finditer(masked):
            verb = m.group(0).split()[-1]
            if re.match(rf"^(?:{INVOLUNTARY})$", verb, re.I):
                continue
            lo, hi = _clause_bounds(masked, m.start())
            clause = text[lo:hi]
            if _is_question(clause) or OFFER.search(clause):
                continue
            if ATTEMPT_MARKER.search(text[max(0, m.start() - 32):m.start()]):
                continue
            if _player_verb(player_line, verb):
                continue
            # An attempt marker AFTER the verb belongs to this act: "you try to
            # open", "you fail to reach". Scoped to the next 30 characters, not to
            # the whole clause: a clause-wide test read "You push back ... ready
            # to make an entry" as an attempt at pushing, because "ready to"
            # appears at the end and describes something else entirely.
            if pattern is _ACTION_SECOND and ATTEMPT_MARKER.search(
                    text[m.end():m.end() + 30]):
                continue
            out.append(Finding("narrated-action", " ".join(clause.split())[:180],
                               f"unstated act: {m.group(0).strip()!r}",
                               (m.start(), m.end())))
    return out


# ── rule 4: pre-resolved action ───────────────────────────────────────────────

_REPLY_ATTEMPT = re.compile(rf"\byou\s+(?:{ATTEMPT_IN_REPLY})\b", re.I)
_REPLY_OUTCOME = re.compile(rf"\byou\s+(?:{OUTCOME})\b|\bit\s+(?:{OUTCOME})\b"
                            rf"|\bthe\s+\w+\s+(?:{OUTCOME})\b", re.I)


#: The result stated as a bare fragment, which is how this genre's terse prose
#: puts it: "A name, inked." has no verb at all, so a verb-shaped outcome list
#: misses exactly the sentences a small local model reaches for.
RESULT_FRAGMENT = re.compile(
    r"\b(?:a|an|the)\s+[\w'-]+(?:\s+[\w'-]+)?\s*,\s*"
    r"(?:inked|written|spelled|spelt|visible|readable|open|shut|locked|clear|"
    r"bare|signed|damp|gone|moved|shifted|ajar)\b"
    r"|\byour\s+[\w'-]+\s*,\s*\w+ed\b", re.I)


def _clause_of(masked: str, text: str, pos: int) -> str:
    return text[slice(*_clause_bounds(masked, pos))]


def _without(text: str, pattern) -> str:
    """`text` with every match of `pattern` blanked out, or None if none matched."""
    if not text:
        return None
    chars = list(text)
    hits = 0
    for m in pattern.finditer(text):
        hits += 1
        for i in range(m.start(), m.end()):
            chars[i] = " "
    return "".join(chars) if hits else None


def _result(text: str):
    """(match, clause) for a stated result, or None."""
    masked = _mask(text)
    for pattern in (_REPLY_OUTCOME, RESULT_FRAGMENT):
        m = pattern.search(text)
        if m and not _is_question(_clause_of(masked, text, m.start())) \
                and not OFFER.search(_clause_of(masked, text, m.start())):
            return m, _clause_of(masked, text, m.start())
    return None


def preresolved_findings(player_line: str, text: str, rolled: bool = False) -> list:
    """The reply answered the question the player asked.

    Scoped to the whole reply rather than to one sentence, because the shape is
    the beat, not the sentence: "You lean over the ledger. A name, inked." puts
    the attempt and the outcome two sentences apart and is still the defect. The
    reply is one to four sentences about the single action the player just took,
    so the window is already narrow.

    Three guards keep this from being a synonym for "the DM used a success
    verb". `rolled` suppresses it entirely once the engine has resolved a check,
    because then the outcome is earned and naming it is the whole job of the
    narration. The player's own line must contain the intent, so a player who
    typed "I open the door and it opens" has already resolved it themselves. And
    the outcome word must be absent from that same line, for the same reason.
    """
    # Every intent phrase is cut out before looking for an outcome in the
    # player's line, because the two share vocabulary: "I try to SEE if a name is
    # written" states an intent, and "see" is an outcome word. Testing the raw
    # line would read every "see if" as the player having already answered, and
    # the rule would never fire on the most natural phrasing of an attempt. All
    # of the phrases go, not just the first, because "try to see if" contains
    # two of them and cutting one leaves the other in place.
    rest = _without(player_line, PLAYER_INTENT)
    if rolled or rest is None:
        return []
    if _OUTCOME.search(rest):
        return []
    text = text or ""
    masked = _mask(text)
    attempt = next((m for m in _REPLY_ATTEMPT.finditer(masked)
                    if not _is_question(_clause_of(masked, text, m.start()))
                    and not OFFER.search(_clause_of(masked, text, m.start()))), None)
    if attempt is None:
        return []
    found = _result(text)
    if not found:
        return []
    m, _ = found
    return [Finding("pre-resolved", " ".join(text.split())[:200],
                    f"attempt and outcome in one beat: {m.group(0)!r}",
                    (attempt.start(), m.end()))]


# ── the whole lens ────────────────────────────────────────────────────────────

def detect(player_line: str, text: str, pc_names=(), rolled: bool = False) -> list:
    """Every finding in one reply, in KINDS order, de-duplicated by span.

    `rolled` means "the engine has already resolved a check for this beat", and
    it is the caller's job to know: the local-DM loop returns the pre-roll beat
    and the post-roll narration as separate chunks, and only the second one is
    entitled to say what the check found.
    """
    found = (dialogue_findings(player_line, text, pc_names)
             + emotion_findings(text, pc_names)
             + action_findings(player_line, text, pc_names)
             + preresolved_findings(player_line, text, rolled))
    found.sort(key=lambda f: (f.span[0], f.span[1]))
    out, seen = [], set()
    for f in found:
        key = (f.kind, f.span)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def kinds_present(findings) -> list:
    return [k for k in KINDS if any(f.kind == k for f in findings)]
