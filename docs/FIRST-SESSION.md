# Starting a campaign with the display

This is how to open a new campaign with the cinematic display in your browser: the GM runs in your terminal chat, and the story, party sidebar, dice and player input appear on `http://localhost:5001`.

## 1. Install (once)

You need Python 3.10 or newer.

```bash
pip3 install -r display/requirements.txt
python3 systems/dnd5e/build_srd.py --no-fvtt     # SRD data for grid combat (network)
```

## 2. Start the display

From the repo root:

```bash
bash display/start-display.sh
```

It starts the server in the background, prints its address and opens the page. Other ways to start it:

| Command | Use it for |
|---------|-----------|
| `bash display/start-display.sh --lan` | Phones and tablets on your home network (open `http://<your-ip>:5001` on them) |
| `bash display/start-display.sh --campaign NAME` | Resuming an existing campaign: shows its last exchanges and checks the SRD data |
| `GM_DISPLAY_PORT=5055 bash display/start-display.sh` | Another port, if 5001 is taken |

The server log is `display/app.log`. To stop it: `kill $(cat display/app.pid)`. Starting it again stops any display already running, on any port.

Until a campaign is loaded, the page can still show the last campaign you played. That is expected: step 3 clears it.

## 3. Create the campaign

In your GM chat (OpenCode, Claude Code, ...):

```
/gm new ember-hollow
```

The GM asks for the system, party size, tone and so on, then writes the campaign to `~/open-tabletop-gm/campaigns/ember-hollow/` (set `GM_CAMPAIGN_ROOT` to keep campaigns somewhere else). When the display is running, it switches the display to the new campaign and wipes the previous one's text and party. Then create a character:

```
/gm character new
```

In later sessions, `/gm load ember-hollow` brings back the party, the date, the quests and the last few exchanges.

## 4. Play

- **Story**: narration types out in the middle. NPC lines carry the speaker's name, and a collapsed **DM hint** block lists options when tutor mode is on.
- **Sidebar**: HP, AC and spell slots for each character, plus factions and quests. The in-world date is top right.
- **Acting from the browser**: open **Party input** at the bottom, pick your character, type what you do, press **Stage**, then **Ready**. When everyone is ready the action shows as queued. The GM picks it up at the start of its next turn (`display/check_input.py`). You can also just type in the GM chat.
- **Rolling dice**: when the GM calls for a check, a "Waiting on: <name>" badge appears. Press **Phone Mode** (top right) and choose your character: the Roll tab is already filled in (die, modifier, DC). Press **Roll** and the GM gets the result. On a real phone, start the display with `--lan` and open the same page there.
- **Grid fights**: see [TACTICAL-COMBAT.md](TACTICAL-COMBAT.md). To learn the grid rules on your own first, play [the tutorial](TUTORIAL.md).

## Check the display without a GM

These are the calls the GM makes at the start of a campaign. Run them yourself to see the display work end to end without a language model. A spare port and a throwaway campaign folder keep your real campaigns untouched:

```bash
export GM_DISPLAY_PORT=5080 GM_CAMPAIGN_ROOT=/tmp/otgm-test
mkdir -p $GM_CAMPAIGN_ROOT/campaigns/demo/characters
cp templates/state.md $GM_CAMPAIGN_ROOT/campaigns/demo/
python3 display/gm-display-app.py > $GM_CAMPAIGN_ROOT/app.log 2>&1 &
sleep 2 && open http://localhost:5080     # or open it in any browser

python3 display/send.py --set-campaign demo < /dev/null
python3 display/push_stats.py --clear
python3 display/push_stats.py --replace-players --json '{"players":[{"name":"Kairos","race":"Kenku","class":"Wizard","level":1,"hp":{"current":8,"max":8,"temp":0},"ac":12,"initiative":"+2","speed":30}]}'
python3 display/push_stats.py --quests '[{"name":"The Missing Lamplighter","status":"active"}]'
echo "Rain needles the shutters of the Last Lantern inn." | python3 display/send.py
echo '"Maddoc never came back from the ridge."' | python3 display/send.py --npc "Innkeeper Bress"

# Waits until someone rolls in Phone Mode, then prints the roll
python3 display/send.py --dice-request --character Kairos --spec 1d20 --modifier 1 --label "Insight check" --dc 12 --wait

# After Stage + Ready in Party input: prints "[Kairos]: ..."
python3 display/check_input.py
```

Stop it with `kill %1` (same terminal) and delete `/tmp/otgm-test` when you are done. This starts the server directly rather than through `start-display.sh`, because that script stops every other running display first.
